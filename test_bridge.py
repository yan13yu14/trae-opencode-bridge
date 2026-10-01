"""测试客户端：模拟 Trae 通过 MCP stdio 协议调用 bridge_server.py。

用法:
    python test_bridge.py check            # 握手 + tools/list + opencode_check
    python test_bridge.py rescue           # 端到端：只读诊断模式（默认，不应改动项目文件）
    python test_bridge.py rescue fix       # 端到端：直接修复模式（opencode 会真实修改项目）
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).parent
ARGS = sys.argv[1:]
MODE = ARGS[0] if ARGS else "check"
FIX_MODE = len(ARGS) > 1 and ARGS[1] == "fix"

proc = subprocess.Popen(
    ["uv", "run", str(HERE / "bridge_server.py")],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=open(HERE / "logs" / "server_stderr.log", "a", encoding="utf-8"),
    text=True,
    encoding="utf-8",
    cwd=HERE,
)

# 全局看门狗，防止测试卡死（守护线程，出错时脚本可立即退出）
watchdog = threading.Timer(360, proc.kill)
watchdog.daemon = True
watchdog.start()


def send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def read_until(want_id):
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(
                "服务器关闭了连接（启动失败，查看 logs/server_stderr.log）"
            )
        msg = json.loads(line)
        if msg.get("id") == want_id:
            return msg


def show(name, resp):
    print(f"\n===== {name} =====")
    print(json.dumps(resp, ensure_ascii=False, indent=2)[:4000])


try:
    # 1. initialize
    send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bridge-test", "version": "0.1"},
            },
        }
    )
    show("initialize", read_until(1))
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 2. tools/list
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    show("tools/list", read_until(2))

    # 3. opencode_check
    send(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "opencode_check", "arguments": {}},
        }
    )
    show("opencode_check", read_until(3))

    # 3b. opencode_config show
    send(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "opencode_config", "arguments": {"action": "show"}},
        }
    )
    show("opencode_config(show)", read_until(5))

    # 3c. opencode_config set_model（故意写错，验证校验逻辑）
    send(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "opencode_config",
                "arguments": {"action": "set_model", "model": "nonexistent-model-xyz"},
            },
        }
    )
    show("opencode_config(set_model 无效模型)", read_until(6))

    # 3d. show + filter 按关键字过滤模型列表
    send(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "opencode_config",
                "arguments": {"action": "show", "filter": "glm-5.3"},
            },
        }
    )
    show("opencode_config(show filter=glm-5.3)", read_until(7))

    # 3e. set_model 简写同名多 provider → 应自动选择已配 key 的直连 provider（幂等）
    send(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "opencode_config",
                "arguments": {"action": "set_model", "model": "glm-5.3-flash"},
            },
        }
    )
    show("opencode_config(set_model 简写自动选择)", read_until(8))

    # 4. opencode_rescue（真实调用 opencode，在隔离沙盒项目中排障）
    if MODE == "rescue":
        sandbox = Path("D:/temp/bridge-sandbox")
        send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "opencode_rescue",
                    "arguments": {
                        "problem": "在沙盒项目里运行 python app.py 打印 HTTP 状态码失败，"
                        "请帮我定位并给出修复方案",
                        "context": "报错信息：\n"
                        "Traceback (most recent call last):\n"
                        '  File "D:\\temp\\bridge-sandbox\\app.py", line 6, in main\n'
                        "    print(resp.statuz_code)\n"
                        "AttributeError: 'Response' object has no attribute 'statuz_code'\n"
                        "已尝试：查阅 requests 文档未找到 statuz_code 属性。",
                        "workdir": str(sandbox),
                        "readonly": not FIX_MODE,
                    },
                },
            }
        )
        show("opencode_rescue", read_until(4))
finally:
    watchdog.cancel()
    proc.terminate()
    print("\n[测试完成]")
