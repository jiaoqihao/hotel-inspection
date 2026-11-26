"""Round-trip the installed MCP SDK over stdio against a disposable loopback store."""

import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


async def client(port):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Redirect only this child's fixed loopback connection to the isolated test server.
    script = (
        "import asyncio,http.client;from local import recording_mcp as m;"
        f"m.HTTPConnection=lambda host,port,timeout:http.client.HTTPConnection('127.0.0.1',{port},timeout=timeout);"
        "asyncio.run(m.main())"
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-c", script], cwd=str(ROOT)
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        names = {t.name for t in tools}
        assert {
            "hotel_overview",
            "list_recordings",
            "recording_module",
            "device_position",
            "operation_status",
        } <= names

        async def run(name, args):
            result = await session.call_tool(name, args)
            assert not result.is_error, result.content
            return result.structured_content

        overview = await run("hotel_overview", {})
        assert isinstance(overview["badge"], int)
        legacy = await run("list_recordings", {})
        assert legacy == {"records": [], "nextOffset": None}
        device = (await run("device_list", {}))["data"]["devices"][0]
        args = dict(
            number=device["number"],
            revision=device["revision"],
            position="前台",
            requestKey=uuid4().hex,
        )
        result = await run("device_position", args)
        assert (
            result["data"]["after"] == "前台"
            and await run("device_position", args) == result
        )
        pending = await run("wecom_test", dict(requestKey=uuid4().hex))
        assert pending["state"] == "pending" and "#ai/action/" in pending["confirmUrl"]
        assert (await run("operation_status", dict(call=pending["call"])))[
            "state"
        ] == "pending"
        print(
            "PASS stdio initialize, list",
            len(names),
            "tools, legacy signatures, direct update, replay and confirmation",
        )


def smoke():
    import uvicorn

    with tempfile.TemporaryDirectory() as temp:
        os.environ["HOTEL_VOICE_DATA"] = temp
        from local import server

        with server.connection() as db:
            db.execute("INSERT INTO badge_sync_config VALUES(1,'test',X'00','[]',1,0)")
            db.execute(
                "INSERT INTO badge_devices(app,number,source,name,position,test) VALUES('test','TEST-MCP','挂牌','测试设备','餐厅',1)"
            )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        service = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
        worker = threading.Thread(
            target=service.run, kwargs={"sockets": [sock]}, daemon=True
        )
        worker.start()
        try:
            for _ in range(100):
                if service.started:
                    break
                time.sleep(0.1)
            python = (
                Path(os.environ["LOCALAPPDATA"])
                / "HotelVoice/mcp-venv/Scripts/python.exe"
            )
            subprocess.run(
                [str(python), "-m", "local.agent_mcp_smoke", str(port)],
                cwd=ROOT,
                check=True,
                timeout=45,
            )
        finally:
            service.should_exit = True
            worker.join(15)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(client(int(sys.argv[1])))
    else:
        smoke()
