#!/usr/bin/env python3
"""MCP 服务的冒烟测试：像客户端那样真的 spawn 一次，走 stdio 对话。

    python tools/mcp_smoke_test.py

检查四件事：``initialize`` 有回应、``tools/list`` 有 14 个工具、
``tools/call`` 能跑只读工具、中文在管道里不坏（UTF-8 往返）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):   # Windows 控制台默认 GBK，中文/emoji 会炸
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_server.py"
EXPECTED_TOOLS = 14


class Client:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ROOT), text=True, encoding="utf-8", bufsize=1,
        )
        self.next_id = 1

    def request(self, method: str, params: dict | None = None) -> dict:
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.next_id += 1
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            raise SystemExit(f"服务没有回应 {method}；stderr:\n{err}")
        return json.loads(line)

    def notify(self, method: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        self.proc.terminate()


def main() -> int:
    client = Client()
    failures: list[str] = []
    try:
        init = client.request("initialize", {"protocolVersion": "2024-11-05",
                                            "clientInfo": {"name": "smoke", "version": "0"}})
        info = init.get("result", {}).get("serverInfo", {})
        print(f"initialize → {info.get('name')} {info.get('version')} "
              f"(protocol {init.get('result', {}).get('protocolVersion')})")
        if info.get("name") != "comfyuimanager":
            failures.append("serverInfo.name 不对")

        client.notify("notifications/initialized")

        listed = client.request("tools/list")
        tools = [t["name"] for t in listed.get("result", {}).get("tools", [])]
        print(f"tools/list → {len(tools)} 个：{', '.join(tools)}")
        if len(tools) != EXPECTED_TOOLS:
            failures.append(f"工具数不是 {EXPECTED_TOOLS}")
        for tool in listed["result"]["tools"]:
            if not tool.get("inputSchema", {}).get("type") == "object":
                failures.append(f"{tool['name']} 的 inputSchema 不是对象")

        status = client.request("tools/call", {"name": "cman_status", "arguments": {}})
        payload = status.get("result", {})
        if payload.get("isError"):
            failures.append(f"cman_status 报错：{payload}")
        else:
            data = json.loads(payload["content"][0]["text"])
            print(f"cman_status → 工作区 {data.get('workspace')}；计数 {data.get('counts')}")

        instances = client.request("tools/call", {"name": "cman_list_instances", "arguments": {}})
        data = json.loads(instances["result"]["content"][0]["text"])
        names = [i.get("cn_name") for i in data.get("instances", [])]
        print(f"cman_list_instances → {len(names)} 个，中文名往返：{names}")
        for name in names:
            if name and any(ord(ch) == 0xFFFD for ch in str(name)):
                failures.append("中文被解码坏了（出现 U+FFFD）")

        bad = client.request("tools/call", {"name": "cman_delete_instance",
                                            "arguments": {"env": "CreativeSpace"}})
        result = bad.get("result", {})
        if not result.get("isError"):
            failures.append("未确认的删除居然通过了（confirm 门槛失效）")
        else:
            print(f"删除护栏 → {result['content'][0]['text'][:40]}…")
    finally:
        client.close()

    if failures:
        print("\n失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
