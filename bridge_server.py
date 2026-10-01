# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0,<2"]
# ///
"""opencode-bridge: 让 Trae 在卡住时向 opencode 求助的 MCP 服务器（可分发版）。

- Trae 为主 Agent，opencode 为辅助排障专家
- 自动检测本机 opencode 配置（凭据/模型）；他人拿到后可在 Trae 里输 key、换模型
- 通过 MCP stdio 协议与 Trae 通信，工具返回值即回传给 Trae 的"方案日志"
"""

import datetime
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------- 配置（可用环境变量覆盖） ----------
OPENCODE_BIN = os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or "opencode"
DEFAULT_WORKDIR = os.environ.get("BRIDGE_WORKDIR", str(Path(__file__).parent))
TIMEOUT = int(os.environ.get("BRIDGE_TIMEOUT", "600"))  # opencode 单次执行超时(秒)
MAX_CONTEXT_CHARS = int(os.environ.get("BRIDGE_MAX_CONTEXT", "20000"))

BRIDGE_CONFIG = (
    Path(__file__).parent / "bridge_config.json"
)  # 桥接自身配置（默认模型等）
AUTH_JSON = Path.home() / ".local" / "share" / "opencode" / "auth.json"  # opencode 凭据
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

COMMON_PROVIDERS = (
    "anthropic",
    "openai",
    "openrouter",
    "deepseek",
    "zhipuai",
    "google",
    "mistral",
    "groq",
    "xai",
    "ollama",
)

mcp = FastMCP("opencode-bridge")

PROMPT_TEMPLATE = """你是被主 Agent（Trae）调用的辅助排障专家。Trae 在下面的任务上卡住了，请你独立分析并给出可执行的解决方案。

【卡住的问题】
{problem}

【Trae 最近的会话上下文 / 报错 / 已尝试的方案】
{context}

请输出：
1. 一句话诊断（问题根源）
2. 具体修复步骤（可直接执行的命令或代码改动）
3. 若信息不足以定位，列出需要补充执行的最小诊断命令
"""

READONLY_SUFFIX = """

【运行约束】你处于"只读诊断"模式（文件编辑权限已被禁用）：
- 禁止修改、创建、删除任何项目文件；
- 禁止执行有副作用的命令（安装依赖、写文件、改配置等）；
- 只允许读取文件、检索代码、运行无副作用的诊断命令；
- 将诊断结论和修复步骤直接写在最终回复里，由 Trae 去执行。
"""


def _clean(text: str) -> str:
    return ANSI_RE.sub("", text or "").strip()


def _load_bridge_cfg() -> dict:
    try:
        return json.loads(BRIDGE_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_bridge_cfg(cfg: dict) -> None:
    BRIDGE_CONFIG.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _mask(key: str) -> str:
    return f"{key[:4]}...{key[-4:]}" if key and len(key) > 12 else "***"


def _list_models() -> list:
    """列出本机 opencode 可用的模型（provider/model 格式）。"""
    try:
        proc = subprocess.run(
            [OPENCODE_BIN, "models"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    except Exception:
        return []


def _auth_providers() -> list:
    """已配置凭据的 provider id 列表。"""
    try:
        return list(json.loads(AUTH_JSON.read_text(encoding="utf-8")).keys())
    except Exception:
        return []


def _rank_models(cands: list):
    """同名模型多 provider 匹配时排序：当前默认模型的 provider > 已配 key 的直连 provider > 其他。
    返回 (唯一胜者或空, 排序后的完整列表, 胜出说明)。"""
    authed = _auth_providers()
    cur = _load_bridge_cfg().get("model", "").split("/")[0]

    def score(m):
        p = m.split("/")[0]
        if p == cur:
            return 0
        if p in authed and p != "opencode":
            return 1
        return 2

    ordered = sorted(cands, key=score)
    best = score(ordered[0])
    winners = [m for m in ordered if score(m) == best] if best < 2 else []
    if len(winners) == 1:
        note = (
            "与当前默认模型同 provider"
            if best == 0
            else "已配置凭据的直连 provider（优先于 opencode 网关）"
        )
        return winners[0], ordered, note
    return "", ordered, ""


def _resolve_model(want: str):
    """把用户给的模型名解析成完整 provider/model。返回 (完整id, 提示或错误)。
    成功时 id 非空（msg 可能为自动选择说明）；失败时 id 为空、msg 为错误。"""
    if not want:
        return "", ""
    models = _list_models()
    if not models:
        return (
            "",
            "无法获取模型列表（opencode 未配置或不可用），请先调用 opencode_config(action='set_key') 配置凭据",
        )
    if want in models:
        return want, ""
    if "/" not in want:
        cands = [m for m in models if m.split("/", 1)[-1] == want]
        if len(cands) == 0:
            return (
                "",
                f"'{want}' 不在可用模型列表中，可调用 opencode_config(action='show', filter='{want}') 查看匹配的模型",
            )
        if len(cands) == 1:
            return cands[0], ""
        ordered_pick, ordered, note = _rank_models(cands)
        if ordered_pick:
            return (
                ordered_pick,
                f"自动选择 {ordered_pick}（{note}）；如需其他 provider 请用完整 provider/model",
            )
        rec = f"，推荐: {ordered[0]}" if ordered else ""
        return (
            "",
            f"'{want}' 匹配到多个模型: {', '.join(ordered)}，请使用完整 provider/model 格式{rec}",
        )
    return (
        "",
        f"'{want}' 不在可用模型列表中，可调用 opencode_config(action='show', filter='{want}') 查看全部可用模型",
    )


def _auth_summary() -> list:
    """读取 opencode 凭据概况（key 打码）。"""
    try:
        auth = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
        return [f"{p} (key: {_mask(v.get('key', ''))})" for p, v in auth.items()]
    except Exception:
        return []


# ---------- 求助工具 ----------


@mcp.tool()
def opencode_rescue(
    problem: str,
    context: str,
    workdir: str = "",
    readonly: bool = True,
    model: str = "",
) -> str:
    """当你在同一问题上连续失败、陷入重复循环、或遇到无法解决的环境配置/依赖/疑难报错时，
    调用此工具向辅助 Agent（opencode）求助，并把返回的方案作为重要参考执行。

    Args:
        problem: 清楚描述卡住的问题（想做什么、卡在哪一步）。
        context: 最近的关键上下文：报错信息、执行过的命令及输出、已尝试失败的方案。越具体越好。
        workdir: 当前项目根目录的绝对路径（opencode 将在该目录下分析，可读取项目文件）。
        readonly: 默认 true＝只读诊断模式：opencode 只分析并返回修复步骤（文件编辑权限被硬性禁用），
            由你来执行修改。仅当用户明确同意让 opencode 直接动手修复时才传 false。
        model: 可选，本次求助使用的模型（格式 provider/model，如 zhipuai-coding-plan/glm-5.3-flash；
            也支持简写如 glm-5.3-flash）。留空则使用 opencode_config 里设置的默认模型或 opencode 自身默认。
    """
    wd_note = ""
    workdir = workdir or DEFAULT_WORKDIR
    if not Path(workdir).is_dir():
        wd_note = f"(传入的 workdir 不存在，已回退到 {DEFAULT_WORKDIR})"
        workdir = DEFAULT_WORKDIR

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n...(上下文过长，已截断)"

    use_model, mmsg = _resolve_model(model or _load_bridge_cfg().get("model", ""))
    if not use_model and mmsg:
        return f"[opencode-bridge] 模型配置错误: {mmsg}"

    prompt = PROMPT_TEMPLATE.format(problem=problem, context=context)
    cmd = [OPENCODE_BIN, "run"]
    if readonly:
        cmd += ["--agent", "plan"]  # plan agent：文件编辑权限被硬性禁用
        prompt += READONLY_SUFFIX
    if use_model:
        cmd += ["-m", use_model]
    cmd.append(prompt)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"rescue_{ts}.log"

    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
        )
        mode = "只读诊断" if readonly else "直接修复"
        model_note = use_model or "opencode默认"
        status = f"成功 (exit={proc.returncode}, 模式={mode}, 模型={model_note})"
        if mmsg:
            status += f"（{mmsg}）"
        out = _clean(proc.stdout)
        err = _clean(proc.stderr)
        body = out if out else err or "(opencode 无输出)"
    except subprocess.TimeoutExpired:
        status = f"超时 (>{TIMEOUT}s)"
        body = "opencode 执行超时，可通过环境变量 BRIDGE_TIMEOUT 调大后重试。"
    except Exception as e:
        status = f"调用失败: {type(e).__name__}: {e}"
        body = ""

    log_path.write_text(
        f"[{ts}] 状态: {status}\n[workdir] {workdir}\n[problem] {problem}\n\n{body}\n",
        encoding="utf-8",
    )
    return (
        f"[opencode-bridge] 状态: {status} {wd_note}\n"
        f"日志已保存: {log_path}\n\n"
        f"--- opencode 给出的方案 ---\n{body}"
    )


# ---------- 配置工具 ----------


@mcp.tool()
def opencode_config(
    action: str,
    model: str = "",
    provider: str = "",
    api_key: str = "",
    filter: str = "",
) -> str:
    """查看/配置辅助 Agent（opencode）的模型与凭据。分发给他人使用时，先在 Trae 里调用本工具完成配置。

    Args:
        action: "show"=查看当前配置与可用模型；"set_model"=设置默认模型(需 model)；
            "set_key"=写入某 provider 的 API Key(需 provider+api_key)；"clear_key"=删除某 provider 的 Key(需 provider)。
        model: 模型，格式 provider/model（如 zhipuai-coding-plan/glm-5.3）或简写（如 glm-5.3，多 provider 同名时自动选择已配 key 的直连 provider）。
        provider: provider id，如 anthropic / openai / openrouter / deepseek / zhipuai 等。
        api_key: 对应 provider 的 API Key（仅保存在本机 opencode 凭据文件中，输出时打码）。
        filter: 可选，show 时按关键字过滤模型列表（如 filter="glm" 只列含 glm 的模型），避免长列表截断。
    """
    if action == "show":
        models = _list_models()
        cfg_model = _load_bridge_cfg().get("model", "")
        if filter:
            flt = filter.lower()
            models = [m for m in models if flt in m.lower()]
            lines = [
                f"默认模型: {cfg_model or '（未单独设置，使用 opencode 自身默认）'}",
                f"已配置凭据: {'; '.join(_auth_summary()) or '（无）'}",
                f"可用模型 (匹配 '{filter}'，共 {len(models)} 个):",
                *models[:60],
            ]
            if len(models) > 60:
                lines.append(f"... 其余 {len(models) - 60} 个略")
            if not models:
                lines.append(f"没有匹配 '{filter}' 的模型，可去掉 filter 查看全部")
            return "\n".join(lines)
        lines = [
            f"默认模型: {cfg_model or '（未单独设置，使用 opencode 自身默认）'}",
            f"已配置凭据: {'; '.join(_auth_summary()) or '（无）'}",
            f"可用模型 ({len(models)} 个):",
            *(", ".join(models[:30]).split(", ") if models else []),
        ]
        if len(models) > 30:
            lines.append(
                f"... 其余 {len(models) - 30} 个略（可用 filter 参数按关键字过滤）"
            )
        if not models:
            lines.append(
                "\n未检测到可用模型！请向用户询问要使用的 AI 服务商与 API Key，"
                "然后调用 opencode_config(action='set_key', provider='...', api_key='...')。"
                f"常见 provider: {', '.join(COMMON_PROVIDERS)}"
            )
        return "\n".join(lines)

    if action == "set_model":
        full, msg = _resolve_model(model)
        if not full:
            return f"[配置失败] {msg}"
        cfg = _load_bridge_cfg()
        cfg["model"] = full
        _save_bridge_cfg(cfg)
        ok = f"[配置成功] 默认求助模型已设为 {full}（之后所有 opencode_rescue 默认使用它）"
        return f"{ok}；{msg}" if msg else ok

    if action == "set_key":
        if not provider or not api_key:
            return "[配置失败] 需要 provider 和 api_key 两个参数"
        try:
            auth = {}
            if AUTH_JSON.exists():
                auth = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
            auth[provider] = {"type": "api", "key": api_key}
            AUTH_JSON.parent.mkdir(parents=True, exist_ok=True)
            AUTH_JSON.write_text(json.dumps(auth, indent=2), encoding="utf-8")
        except Exception as e:
            return f"[配置失败] 写入凭据文件出错: {type(e).__name__}: {e}"
        models = [m for m in _list_models() if m.startswith(provider + "/")]
        if models:
            return (
                f"[配置成功] 已保存 {provider} 的 API Key ({_mask(api_key)})，"
                f"检测到 {len(models)} 个可用模型，例如: {', '.join(models[:5])}。"
                f"可用 opencode_config(action='set_model', model='...') 设为默认"
            )
        return (
            f"[已保存] {provider} 的 Key 已写入，但未检测到该 provider 的模型，"
            f"请检查 provider 名是否正确（常见: {', '.join(COMMON_PROVIDERS)}）"
        )

    if action == "clear_key":
        if not provider:
            return "[配置失败] 需要 provider 参数"
        try:
            if AUTH_JSON.exists():
                auth = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
                auth.pop(provider, None)
                AUTH_JSON.write_text(json.dumps(auth, indent=2), encoding="utf-8")
        except Exception as e:
            return f"[配置失败] {type(e).__name__}: {e}"
        return f"[配置成功] 已删除 {provider} 的凭据"

    return f"[配置失败] 未知 action: {action}（可用: show / set_model / set_key / clear_key）"


# ---------- 健康检查 ----------


@mcp.tool()
def opencode_check() -> str:
    """健康检查：确认 opencode 可用、检测凭据与模型配置。接入 Trae 后先调用一次本工具验证链路；
    若提示未配置模型/凭据，按提示引导用户提供 API Key 并调用 opencode_config 完成 配置。"""
    try:
        proc = subprocess.run(
            [OPENCODE_BIN, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        ver = _clean((proc.stdout or "") + (proc.stderr or ""))
    except Exception as e:
        return (
            f"opencode 不可用 ({type(e).__name__}: {e})。"
            "请安装 opencode: https://opencode.ai/docs/ 后重试"
        )

    models = _list_models()
    cfg_model = _load_bridge_cfg().get("model", "")
    info = (
        f"opencode 可用: {ver} | 单次超时: {TIMEOUT}s | 日志目录: {LOG_DIR}\n"
        f"默认求助模型: {cfg_model or '（opencode 自身默认）'} | "
        f"已配置凭据: {'; '.join(_auth_summary()) or '（无）'} | 可用模型: {len(models)} 个"
    )
    if not models:
        info += (
            "\n⚠ 未检测到可用模型：请向用户询问 AI 服务商与 API Key，"
            "调用 opencode_config(action='set_key', provider='...', api_key='...') 完成配置"
        )
    return info


if __name__ == "__main__":
    mcp.run()
