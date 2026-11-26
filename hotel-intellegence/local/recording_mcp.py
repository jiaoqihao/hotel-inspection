"""Local stdio MCP adapter for the recording workspace. Start with -m local.recording_mcp."""

from http.client import HTTPConnection
import json
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from mcp.server import MCPServer
from pydantic import Field


mcp = MCPServer("栖鉴录音工作台")
Offset = Annotated[int, Field(ge=0)]
Limit = Annotated[int, Field(ge=1, le=100)]
RecordingId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


def read_api(path: str, **params):
    client = HTTPConnection("127.0.0.1", 8765, timeout=15)
    try:
        query = "?" + urlencode(params) if params else ""
        client.request("GET", "/api/" + path + query)
        response = client.getresponse()
        if response.status != 200:
            raise ValueError(f"录音工作台返回 HTTP {response.status}")
        return json.load(response)
    finally:
        client.close()


@mcp.tool(structured_output=True)
def hotel_overview() -> dict[str, Any]:
    """读取当前门店有权限查看的摄像头整改、胸牌待复核和电话工单统计。"""
    return read_api("overview")


@mcp.tool(structured_output=True)
def list_recordings(offset: Offset = 0, limit: Limit = 20) -> dict[str, Any]:
    """分页查找录音，返回编号、场景、测试标记及分析状态。使用 nextOffset 读取下一页。"""
    return read_api("badge/record-index", offset=offset, limit=limit)


@mcp.tool(structured_output=True)
def recording_module(recording_id: RecordingId, module: Literal["场景识别", "原话撰写", "录音质量"],
                     offset: Offset = 0, limit: Limit = 30) -> dict[str, Any]:
    """读取一条录音的指定模块。报告包含 AI 判断及片段依据。逐字稿分页包含原话和时间戳。"""
    item = read_api(f"records/{recording_id}")
    result = {"id": recording_id, "status": item["status"], "module": module}
    if module == "原话撰写":
        turns = item.get("segments") or []
        end = offset + limit
        result.update(segments=turns[offset:end], nextOffset=end if end < len(turns) else None)
    elif module == "场景识别":
        result.update(report=item.get("report"), detectedScenes=item.get("detectedScenes", []))
    else:
        result["quality"] = item.get("quality")
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
