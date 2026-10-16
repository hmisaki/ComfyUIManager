#!/usr/bin/env python3
"""ComfyUIManager 的 MCP 服务（stdio，无第三方依赖）。

为什么不用官方 Python SDK：这个仓库的卖点就是"只要标准库 + PySide6"，
而 MCP 的 stdio 传输就是「一行一个 JSON-RPC 消息」，自己实现反而更透明、
也少一个要用户装的东西。协议面覆盖：``initialize`` / ``tools/list`` /
``tools/call`` / ``ping``，外加 ``resources/list`` 与 ``prompts/list`` 的空应答
（有些客户端会探这两个）。

工具全部以 ``cman_`` 开头（cman = ComfyUIManager），语义见 :mod:`core.ops`：
    cman_status / cman_get_config / cman_set_config
    cman_list_templates / cman_list_instances / cman_create_instance
    cman_launch_instance / cman_delete_instance / cman_check_instance
    cman_list_workflows / cman_add_workflow / cman_discover_workflows
    cman_list_models / cman_list_nodes

手动跑（不要用管道灌输入，会被当成 JSON 解析）::

    python mcp_server.py            # 正常是给 MCP 客户端 spawn 用的
    python mcp_server.py --selftest # 自检：跑一遍 initialize / tools/list / 只读工具
    python mcp_server.py --list     # 只打印工具清单
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import ops  # noqa: E402  （tools/list 前不需要 Qt，也不会加载 Qt）

SERVER_NAME = "comfyuimanager"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"

INSTRUCTIONS = (
    "ComfyUIManager：本机 ComfyUI 多实例管理器。概念：模板（一个已存在的 conda 环境 + "
    "一份 ComfyUI 目录）→ 实例（ComfyUIApps/<名字>，克隆出来的环境 + 挂着共享模型目录的 junction）→ "
    "工作流（登记进管理器的 .json）。典型顺序：cman_status 看现状 → cman_list_templates 看可用模板 → "
    "cman_create_instance → cman_launch_instance → cman_check_instance。"
    "创建/删除是分钟级且不可撤销的操作（删除必须显式 confirm=true），"
    "调用方的超时建议放到 30 分钟以上。"
)


class ToolError(RuntimeError):
    pass


def _require(arguments: Dict[str, Any], name: str) -> Any:
    value = arguments.get(name)
    if value is None or value == "":
        raise ToolError(f"缺少必填参数 {name}")
    return value


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def _t_status(a: Dict[str, Any]) -> Any:
    return ops.status()


def _t_get_config(a: Dict[str, Any]) -> Any:
    return ops.get_config()


def _t_set_config(a: Dict[str, Any]) -> Any:
    payload = {k: a[k] for k in ops.CONFIG_KEYS if k in a and a[k] is not None}
    if not payload:
        raise ToolError(f"至少要给一个配置键：{list(ops.CONFIG_KEYS)}")
    return ops.set_config(**payload)


def _t_list_templates(a: Dict[str, Any]) -> Any:
    return ops.list_templates()


def _t_list_instances(a: Dict[str, Any]) -> Any:
    return {"instances": ops.list_instances(heal=bool(a.get("heal", True)))}


def _t_create_instance(a: Dict[str, Any]) -> Any:
    return ops.create_instance(
        template=_require(a, "template"),
        env_name=str(a.get("env_name") or ""),
        cn_name=str(a.get("cn_name") or ""),
        port=int(a["port"]) if a.get("port") else None,
        clone_env=bool(a.get("clone_env", True)),
        timeout=int(a.get("timeout_s") or ops.DEFAULT_CREATE_TIMEOUT),
    )


def _t_launch_instance(a: Dict[str, Any]) -> Any:
    return ops.launch_instance(
        env=_require(a, "env"),
        port=int(a["port"]) if a.get("port") else None,
        open_browser=bool(a.get("open_browser", True)),
        wait_open=int(a.get("wait_open_s") if a.get("wait_open_s") is not None
                      else ops.DEFAULT_LAUNCH_TIMEOUT),
    )


def _t_delete_instance(a: Dict[str, Any]) -> Any:
    return ops.delete_instance(
        env=_require(a, "env"),
        confirm=bool(a.get("confirm", False)),
        remove_env=bool(a.get("remove_env", True)),
    )


def _t_check_instance(a: Dict[str, Any]) -> Any:
    return ops.check_instance(
        env=str(a.get("env") or ""),
        port=int(a["port"]) if a.get("port") else None,
    )


def _t_list_workflows(a: Dict[str, Any]) -> Any:
    items = ops.list_workflows(env=str(a.get("env") or ""), query=str(a.get("query") or ""))
    return {"total": len(items), "workflows": items}


def _t_add_workflow(a: Dict[str, Any]) -> Any:
    return ops.add_workflow(
        path=_require(a, "path"),
        conda_env=str(a.get("conda_env") or ""),
        category=str(a.get("category") or ""),
        description=str(a.get("description") or ""),
    )


def _t_discover_workflows(a: Dict[str, Any]) -> Any:
    return ops.discover_workflows(env=str(a.get("env") or ""),
                                  limit=int(a.get("limit") or 200))


def _t_list_models(a: Dict[str, Any]) -> Any:
    return ops.list_models(
        query=str(a.get("query") or ""),
        model_type=str(a.get("model_type") or ""),
        limit=int(a.get("limit") or 50),
        include_nsfw=bool(a.get("include_nsfw", True)),
    )


def _t_list_nodes(a: Dict[str, Any]) -> Any:
    return ops.list_nodes(
        query=str(a.get("query") or ""),
        category=str(a.get("category") or ""),
        limit=int(a.get("limit") or 50),
    )


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "cman_status",
        "description": "一眼看全：工作区/conda/Git Bash/模型源的探测结果，实例与环境实例数，"
                       "工作流、节点库、模型库的条目数，以及有哪些可用模板。先调这个。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _t_status,
    },
    {
        "name": "cman_get_config",
        "description": "读 launcher_config.json 的配置与探测结果（conda_exe/conda_root/"
                       "git_bash_path/ai_models_root/workspace_path 以及 conda 环境总表）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _t_get_config,
    },
    {
        "name": "cman_set_config",
        "description": "改 launcher_config.json（只允许这几个键）。workspace_path 会自动建目录骨架；"
                       "留空的键表示自动探测。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_path": {"type": "string", "description": "工作区目录"},
                "ai_models_root": {"type": "string", "description": "共享模型库目录"},
                "conda_exe": {"type": "string", "description": "conda 可执行文件路径"},
                "conda_root": {"type": "string", "description": "conda 安装根目录"},
                "git_bash_path": {"type": "string", "description": "Git Bash 的 bash.exe 路径"},
            },
            "additionalProperties": False,
        },
        "handler": _t_set_config,
    },
    {
        "name": "cman_list_templates",
        "description": "列可用模板。硬约定：模板目录名必须等于一个已存在的 conda 环境名"
                       "（创建实例时跑的是 conda create --clone <模板名>），返回里的 env_exists 就是这个校验。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _t_list_templates,
    },
    {
        "name": "cman_list_instances",
        "description": "列已登记的实例：解析后的目录、ComfyUI 入口、端口是否在监听、conda 环境是否存在。",
        "inputSchema": {
            "type": "object",
            "properties": {"heal": {"type": "boolean",
                                    "description": "读取时把可解析的绝对路径自愈回相对路径（默认 true）"}},
            "additionalProperties": False,
        },
        "handler": _t_list_instances,
    },
    {
        "name": "cman_create_instance",
        "description": "从模板创建实例：复制模板目录 → conda create --clone → 给实例挂共享模型目录的 "
                       "junction → 登记。分钟级操作（调大调用方超时）；失败会清理掉复制了一半的目录。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template": {"type": "string", "description": "模板名（同时是 conda 环境名）"},
                "env_name": {"type": "string", "description": "实例名/环境名，缺省等于模板名"},
                "cn_name": {"type": "string", "description": "界面显示用的中文名"},
                "port": {"type": "integer", "description": "端口，缺省自动挑一个没占用的"},
                "clone_env": {"type": "boolean", "description": "是否克隆 conda 环境（默认 true）"},
                "timeout_s": {"type": "integer", "description": "conda 克隆的超时秒数，默认 1800"},
            },
            "required": ["template"],
            "additionalProperties": False,
        },
        "handler": _t_create_instance,
    },
    {
        "name": "cman_launch_instance",
        "description": "启动实例：新开一个控制台窗口跑 python main.py --port N，并轮询端口告诉你是真起来了"
                       "还是还在加载。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "实例名或 conda 环境名"},
                "port": {"type": "integer", "description": "端口，缺省用实例登记的"},
                "open_browser": {"type": "boolean", "description": "是否带 --auto-launch 自动开浏览器（默认 true）"},
                "wait_open_s": {"type": "integer", "description": "等端口打开的秒数，默认 60；0 表示不等"},
            },
            "required": ["env"],
            "additionalProperties": False,
        },
        "handler": _t_launch_instance,
    },
    {
        "name": "cman_delete_instance",
        "description": "删除实例（不可撤销：实例目录 + 可选 conda 环境 + 登记记录）。必须显式 confirm=true。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "实例名或 conda 环境名"},
                "confirm": {"type": "boolean", "description": "确认删除，必须为 true"},
                "remove_env": {"type": "boolean", "description": "是否连 conda 环境一起删（默认 true）"},
            },
            "required": ["env", "confirm"],
            "additionalProperties": False,
        },
        "handler": _t_delete_instance,
    },
    {
        "name": "cman_check_instance",
        "description": "探一次实例的 HTTP（默认 /system_stats），返回是否可达、ComfyUI 版本与显卡显存。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "实例名（与 port 二选一）"},
                "port": {"type": "integer", "description": "直接给端口"},
            },
            "additionalProperties": False,
        },
        "handler": _t_check_instance,
    },
    {
        "name": "cman_list_workflows",
        "description": "列已登记的工作流（可按实例过滤、按关键词搜），并标出文件是否还在。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "只看这个实例的"},
                "query": {"type": "string", "description": "关键词"},
            },
            "additionalProperties": False,
        },
        "handler": _t_list_workflows,
    },
    {
        "name": "cman_add_workflow",
        "description": "把一个已存在的 .json 工作流登记进管理器（只记路径，不复制文件）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "工作流 json 的绝对路径"},
                "conda_env": {"type": "string", "description": "属于哪个实例（留空则从路径里猜）"},
                "category": {"type": "string", "description": "分类，缺省 [实例名]"},
                "description": {"type": "string", "description": "说明，缺省用文件名"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "handler": _t_add_workflow,
    },
    {
        "name": "cman_discover_workflows",
        "description": "扫实例目录下的 ComfyUI/user/default/workflows/*.json，报告哪些还没登记（只读）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "只扫这个实例"},
                "limit": {"type": "integer", "description": "最多返回多少条，默认 200"},
            },
            "additionalProperties": False,
        },
        "handler": _t_discover_workflows,
    },
    {
        "name": "cman_list_models",
        "description": "查模型知识库（LORA/Checkpoint 等），可按名称/说明/触发词搜、按类型过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词（中英文名、说明、触发词、路径）"},
                "model_type": {"type": "string", "description": "如 LORA / Checkpoint / Flux"},
                "limit": {"type": "integer", "description": "最多返回多少条，默认 50"},
                "include_nsfw": {"type": "boolean", "description": "是否包含 NSFW 条目（默认 true）"},
            },
            "additionalProperties": False,
        },
        "handler": _t_list_models,
    },
    {
        "name": "cman_list_nodes",
        "description": "查自定义节点知识库（登记过的 GitHub 节点），可按关键词或分类搜。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词（名称、说明、作者）"},
                "category": {"type": "string", "description": "分类，如 生成/模型"},
                "limit": {"type": "integer", "description": "最多返回多少条，默认 50"},
            },
            "additionalProperties": False,
        },
        "handler": _t_list_nodes,
    },
]

TOOL_INDEX: Dict[str, Callable[[Dict[str, Any]], Any]] = {t["name"]: t["handler"] for t in TOOLS}


def tool_definitions() -> List[Dict[str, Any]]:
    return [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in TOOLS]


# ---------------------------------------------------------------------------
# JSON-RPC / MCP
# ---------------------------------------------------------------------------

def call_tool(name: str, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    handler = TOOL_INDEX.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"没有这个工具：{name}"}], "isError": True}
    try:
        payload = handler(arguments or {})
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return {"content": [{"type": "text", "text": text}]}
    except (ops.OpsError, ToolError) as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # 兜底：不要让一次意外把服务打断
        detail = traceback.format_exc(limit=3)
        _log(f"tool {name} failed: {detail}")
        return {"content": [{"type": "text",
                             "text": f"{type(exc).__name__}: {exc}"}], "isError": True}


def handle_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理一条 JSON-RPC 消息；通知类（没有 id）返回 None。"""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if msg_id is None and method and method.startswith("notifications/"):
        return None

    if method == "initialize":
        requested = params.get("protocolVersion") or PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tool_definitions()}}
    if method == "tools/call":
        name = params.get("name") or ""
        result = call_tool(name, params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"不支持的方法：{method}"}}


def _log(text: str) -> None:
    print(f"[{SERVER_NAME}] {text}", file=sys.stderr, flush=True)


def serve() -> None:
    stdin = sys.stdin
    stdout = sys.stdout
    for stream, kwargs in ((stdin, {"encoding": "utf-8"}),
                           (stdout, {"encoding": "utf-8", "newline": "\n"})):
        try:
            stream.reconfigure(**kwargs)  # type: ignore[union-attr]
        except Exception:
            pass
    _log(f"stdio ready (v{SERVER_VERSION})")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"bad json: {exc}")
            continue
        try:
            response = handle_message(message)
        except Exception as exc:
            _log(f"handler crash: {traceback.format_exc(limit=3)}")
            response = {"jsonrpc": "2.0", "id": message.get("id"),
                        "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def selftest() -> int:
    print(f"{SERVER_NAME} v{SERVER_VERSION}, {len(TOOLS)} 个工具\n")
    init = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": PROTOCOL_VERSION, "clientInfo":
                                      {"name": "selftest", "version": "0"}}})
    print("initialize:", json.dumps(init["result"]["serverInfo"], ensure_ascii=False))
    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    print("tools/list:", ", ".join(t["name"] for t in listed["result"]["tools"]))
    print("notification(no reply):", handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}))
    for name in ("cman_status", "cman_list_instances", "cman_list_templates"):
        result = call_tool(name, {})
        text = result["content"][0]["text"]
        flag = "ERROR " if result.get("isError") else "ok    "
        print(f"\n--- {flag}{name} ---\n{text[:1200]}")
    return 0


def main(argv: List[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    if "--list" in argv:
        for tool in tool_definitions():
            print(f"{tool['name']}: {tool['description'][:60]}…")
        return 0
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
