# trae-opencode-bridge

**让 opencode 成为 Trae 的"外接排障专家"——当 Trae 卡住、陷入死循环、搞不定环境配置时，自动向 opencode 求助。**

*Let opencode be Trae's external troubleshooting expert — when Trae gets stuck, loops forever, or can't fix environment issues, it automatically asks opencode for help.*

[![MCP](https://img.shields.io/badge/MCP-Server-blue)](https://modelcontextprotocol.io) [![Trae](https://img.shields.io/badge/IDE-Trae-green)](https://trae.ai) [![opencode](https://img.shields.io/badge/Agent-opencode-orange)](https://opencode.ai) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 为什么需要它 / Why

用过 Trae 的你可能遇到过：

- **同一个报错修 3 遍还在**——Trae 开始反复尝试相同的命令和修改，陷入死循环
- **环境配置一塌糊涂**——依赖装不上、工具链报错，AI 越修越乱
- 同样的模型，在别的 Agent 里明明能解决

问题往往不在模型，而在 Agent 框架的工具执行与循环控制。这个项目把 **opencode**（一个更强的命令行 Agent）封装成 MCP 工具挂进 Trae：**Trae 仍是主 Agent，卡住时自动"打电话"给 opencode 求救**，拿到方案后继续干活。

*Trae users often hit: the same error "fixed" three times and still failing; the agent repeating identical commands in a loop; environment/dependency issues getting worse with every attempt. The problem is usually the agent framework, not the model. This project wraps **opencode** (a powerful CLI agent) as MCP tools inside Trae: Trae stays the primary agent, and calls opencode for rescue when stuck.*

```
Trae (主 Agent) ──卡住/循环 2 次──▶ opencode_rescue ──▶ opencode 排障
      ▲                                                        │
      └──────────── 诊断方案 + 日志（回传 Trae 执行）◀──────────┘
```

## 功能 / Features

| 工具 | 作用 |
|---|---|
| `opencode_rescue` | 求助主工具：把问题+上下文交给 opencode，返回诊断和修复方案 |
| `opencode_config` | 在 Trae 对话里直接换模型 / 填 API Key（支持模糊匹配、自动选择 provider） |
| `opencode_check` | 链路健康检查，自动检测本机 opencode 凭据与可用模型 |

- **双模式**：默认**只读诊断**（基于 opencode `plan` agent，文件编辑权限被*硬性禁用*，绝不乱改项目）；需要时升级**直接修复**模式
- **自动求助规则**：配套 Trae Rules，"同一问题失败 2 次/检测到循环"自动触发求助，项目级开关只问一次
- **日志存档**：每次求助的方案保存在 `logs/`，可回溯
- **开箱即用**：自动检测本机 opencode 配置；分发给没配过的同事，在 Trae 里说句话就能输 Key

## 快速开始 / Quick Start

**前置**：安装 [opencode](https://opencode.ai/docs/) 并登录任一模型服务；安装 [uv](https://docs.astral.sh/uv/)（`winget install astral-sh.uv`，无需 Python）或 Python 3.10+（`pip install "mcp<2"`）。

**1. 克隆本仓库**，记下 `bridge_server.py` 的绝对路径。

**2. 在 Trae 中添加 MCP 服务器**（设置 → MCP → 添加，JSON 导入）：

```json
{
  "mcpServers": {
    "opencode-bridge": {
      "command": "<uv.exe 绝对路径，终端 where uv 查看>",
      "args": ["run", "<仓库路径>\\bridge_server.py"]
    }
  }
}
```

**3. 把 [`TRAE_RULE.md`](TRAE_RULE.md) 的内容粘贴到 Trae 全局规则**——这是"卡住自动求助"的触发机制。

**4. 验证**：在 Trae 对话说 `调用 opencode_check 检查链路`，返回版本信息即成功。

## 模型与凭据 / Models & Credentials

- 默认使用 opencode 自身配置的模型；说"换个模型"即可切换（支持简写，如 `glm-5.3-flash`）
- 首次在新机器使用：`opencode_check` 会检测到无凭据，按提示在对话里提供 provider 和 API Key 即可（Key 仅存本机，显示时打码）
- 每次求助也可临时指定模型（`opencode_rescue` 的 `model` 参数）

## 常见问题 / FAQ

**Q: "卡住自动求助"是怎么实现的？**
Trae 内部状态外部拿不到，所以通过 MCP 工具 + Rules 实现：规则要求 Trae 在"同一错误失败 2 次/重复循环/环境问题无解"时调用 `opencode_rescue`。这是 AI IDE 生态的标准做法。

**Q: 只读模式真的改不了文件吗？**
是的。只读模式调用 opencode 内置的 `plan` agent，其文件编辑权限在权限系统中被硬性拒绝（deny），不是靠提示词约束。

**Q: 我的数据安全吗？**
求助日志只存本机 `logs/`（已被 .gitignore 排除）；API Key 只写入 opencode 自己的凭据文件，所有输出打码。

## 环境变量 / Env Vars

| 变量 | 默认 | 说明 |
|---|---|---|
| `BRIDGE_TIMEOUT` | `600` | opencode 单次执行超时（秒） |
| `BRIDGE_MAX_CONTEXT` | `20000` | 求助上下文最大字符数 |
| `BRIDGE_WORKDIR` | 脚本目录 | 未传 workdir 时的默认目录 |
| `OPENCODE_BIN` | PATH 自动检测 | opencode 可执行文件路径 |

## License

[MIT](LICENSE)
