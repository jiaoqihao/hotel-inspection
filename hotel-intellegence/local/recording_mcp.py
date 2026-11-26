"""Local stdio bridge. The authenticated host grants local administrator business access."""

import asyncio
from http.client import HTTPConnection
import json
from pathlib import Path
from uuid import uuid4

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

LEGACY = {"hotel_overview", "list_recordings", "recording_module"}


def api(method, path, body=None):
    headers = {}
    if method == "POST":
        headers = {
            "X-Local-Token": api("GET", "/api/status")["token"],
            "Content-Type": "application/json",
        }
    conn = HTTPConnection("127.0.0.1", 8765, timeout=90)
    try:
        conn.request(
            method,
            path,
            json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers,
        )
        response = conn.getresponse()
        data = json.load(response)
        if response.status >= 400:
            raise ValueError(data.get("detail", "本机业务请求失败"))
        return data
    finally:
        conn.close()


async def list_tools(ctx, params):
    definitions = await asyncio.to_thread(api, "GET", "/api/agent/tools")
    items = []
    for tool in definitions:
        if tool.pop("writing", False):
            tool["inputSchema"]["properties"]["requestKey"] = {
                "type": "string",
                "minLength": 8,
                "maxLength": 180,
                "description": "本次业务指令的唯一编号；重试使用同一编号",
            }
            tool["inputSchema"]["required"].append("requestKey")
        items.append(types.Tool(**tool))
    items.append(
        types.Tool(
            name="operation_status",
            description="查询待确认或已执行操作的实际状态",
            inputSchema={
                "type": "object",
                "properties": {"call": {"type": "string", "pattern": "^[a-f0-9]{32}$"}},
                "required": ["call"],
            },
        )
    )
    for name, label in (
        ("choose_local_file", "在本机弹出文件选择器，用户选定用于业务导入的文件"),
        ("enter_private_field", "在本机输入连接参数，只向客户端返回受保护的引用编号"),
    ):
        items.append(
            types.Tool(
                name=name,
                description=label,
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        )
    return types.ListToolsResult(tools=items)


def local_input(name):
    import tkinter as tk
    from tkinter import filedialog, simpledialog
    import httpx

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if name == "enter_private_field":
            value = simpledialog.askstring(
                "栖鉴", "输入门店连接地址或密钥", show="*", parent=root
            )
            return (
                api("POST", "/api/agent/secrets", {"value": value})
                if value
                else {"state": "cancelled"}
            )
        selected = filedialog.askopenfilename(
            parent=root, title="选择要导入栖鉴的本机文件"
        )
        if not selected:
            return {"state": "cancelled"}
        with (
            Path(selected).open("rb") as f,
            httpx.Client(trust_env=False, timeout=120) as client,
        ):
            response = client.post(
                "http://127.0.0.1:8765/api/agent/uploads",
                files={"file": (Path(selected).name, f)},
                headers={"X-Local-Token": api("GET", "/api/status")["token"]},
            )
            if not response.is_success:
                raise ValueError("文件暂时无法导入，请核对大小和门店服务状态")
            return response.json()
    finally:
        root.destroy()


async def call_tool(ctx, params):
    try:
        args = dict(params.arguments or {})
        if params.name in ("choose_local_file", "enter_private_field"):
            outcome = await asyncio.to_thread(local_input, params.name)
        elif params.name == "operation_status":
            key = args["call"]
            if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
                raise ValueError("操作编号无效")
            outcome = await asyncio.to_thread(api, "GET", "/api/agent/calls/" + key)
        else:
            key = args.pop("requestKey", None)
            if not key:
                schema = await asyncio.to_thread(api, "GET", "/api/agent/tools")
                if any(t["name"] == params.name and t["writing"] for t in schema):
                    raise ValueError("写入需要稳定的 requestKey")
            outcome = await asyncio.to_thread(
                api,
                "POST",
                "/api/agent/tools/call",
                dict(name=params.name, arguments=args, requestKey=key or uuid4().hex),
            )
        if outcome.get("state") == "pending":
            outcome["confirmUrl"] = (
                "http://127.0.0.1:8765/#ai/action/" + outcome["call"]
            )
        for ref in outcome.get("evidence", []):
            if ref.get("url", "").startswith("api/agent/"):
                ref["url"] = "http://127.0.0.1:8765/" + ref["url"]
        if isinstance(outcome.get("data"), dict) and str(
            outcome["data"].get("resource", "")
        ).startswith("api/agent/"):
            outcome["data"]["resource"] = (
                "http://127.0.0.1:8765/" + outcome["data"]["resource"]
            )
        content = (
            outcome["data"]
            if params.name in LEGACY and outcome.get("state") == "done"
            else outcome
        )
        error = outcome.get("state") == "failed"
    except (OSError, ValueError, KeyError) as exc:
        content = {"error": str(exc)}
        error = True
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps(content, ensure_ascii=False))
        ],
        structuredContent=content,
        isError=error,
    )


async def main():
    server = Server(
        "栖鉴门店工作台",
        version="1.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
