"""Browser acceptance using a temporary store and deterministic synthetic evidence."""

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import wave
from unittest.mock import patch

import uvicorn
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:8765"
OUT = Path(__file__).resolve().parents[2] / "docs/acceptance"


def verify(root, screens):
    os.environ["HOTEL_VOICE_DATA"] = str(root)
    from local import agent_api, server, vision_api
    from local.agent_tools import CHINA, uid

    key = "TEST-VOICE"
    job = uid()
    day = (datetime.now(CHINA) - timedelta(days=1)).date().isoformat()
    record = dict(
        id=key,
        status="done",
        filename="测试录音 · 前台接待",
        extension=".wav",
        source="挂牌",
        employee="测试员工",
        sceneHint="销售沟通",
        recordedAt=day + "T10:05:00+08:00",
        createdAt=day + "T10:30:00+08:00",
        test=True,
        duration=12,
        size=192000,
        reviewed=False,
        report=None,
        progress=100,
        segments=[
            dict(
                id=0, start=3, end=7, speaker="测试员工", text="您好，请问您有预订吗？"
            )
        ],
    )
    server.save(record)
    with wave.open(str(server.AUDIO / (key + ".wav")), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", ""))
        wav.writeframes(bytes(8000 * 12 * 2))
    folder = vision_api.STORE / job
    folder.mkdir()
    frame = Image.new("RGB", (640, 360), "#e0e9e4")
    draw = ImageDraw.Draw(frame)
    draw.rectangle((170, 40, 470, 330), fill="#91aba1")
    draw.rectangle((186, 58, 452, 308), fill="#48685f")
    draw.rectangle((388, 165, 418, 171), fill="#dce3c9")
    frame.save(folder / "TEST.jpg")
    vision_api.save(
        folder,
        dict(
            id=job,
            filename="测试点位 · 后场门口",
            created=day + "T21:10:00+08:00",
            cameraId="TEST-CAM",
            test=True,
            state="已完成",
            scene="公区卫生",
            kind="定期抓拍",
            review="待复核",
            groups=[
                dict(
                    frames=[dict(file="TEST.jpg", seconds=0)],
                    findings=[dict(detail="合成画面仅用于验证取证定位。")],
                )
            ],
        ),
    )
    with server.connection() as db:
        db.execute("INSERT INTO badge_sync_config VALUES(1,'test',X'00','[]',1,0)")
        db.execute(
            "INSERT INTO badge_devices(app,number,source,name,position,test) VALUES('test','TEST-01','挂牌','培训挂牌','餐厅',1)"
        )

    def tool(name, args):
        return dict(
            role="assistant",
            content="",
            tool_calls=[
                dict(
                    type="function",
                    id=uid(),
                    function=dict(
                        name=name, arguments=json.dumps(args, ensure_ascii=False)
                    ),
                )
            ],
        )

    async def fixture(client, context, functions, on_text=None, stream=True):
        if not stream:
            return {"content": "YES"}
        q = [m["content"] for m in context if m["role"] == "user"][-1]
        if context[-1]["role"] != "tool":
            if "岗位" in q:
                return tool(
                    "device_position",
                    dict(number="TEST-01", position="前台", revision=1),
                )
            if "巡检" in q:
                return tool("vision_report", dict(job_id=job))
            return tool("recording_module", dict(recording_id=key, module="原话撰写"))
        if "岗位" in q:
            answer = "已将培训挂牌 TEST-01 的岗位由餐厅改为前台。[1]"
        elif "巡检" in q:
            answer = "昨天 21:10，测试点位留存了 1 组巡检画面。[1]\n本次取样仅覆盖后场门口。其他时段缺少画面，仍需结合现场记录复核。"
        else:
            answer = "昨天的测试录音中，前台在问候后主动确认了客人的预订情况。[1]\n\n### 话术表现\n员工在 00:03 说：“您好，请问您有预订吗？”这句话包含问候和需求确认，适用于入住接待的开场。[1]\n\n### 样本范围\n本次仅检查 1 段测试录音。后续是否核验订单、说明早餐时间或完成房卡交接，还需要其他片段支持。"
        if on_text:
            on_text(answer[:12])
            await asyncio.sleep(0.2)
            on_text(answer)
        return dict(role="assistant", content=answer)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    upstream = "http://127.0.0.1:" + str(sock.getsockname()[1])
    service = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
    worker = threading.Thread(
        target=service.run, kwargs={"sockets": [sock]}, daemon=True
    )
    with patch.object(agent_api, "model_turn", fixture):
        worker.start()
        try:
            for _ in range(100):
                if service.started:
                    break
                time.sleep(0.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                ctx = browser.new_context(
                    viewport=dict(width=1440, height=920), service_workers="block"
                )
                ctx.route(
                    BASE + "/**",
                    lambda route: route.continue_(
                        url=route.request.url.replace(BASE, upstream, 1)
                    ),
                )
                page = ctx.new_page()
                page.goto(BASE + "/#ai")
                expect(
                    page.get_by_role("heading", name="今天想了解门店的什么情况？")
                ).to_be_visible()
                assert (
                    Path(
                        page.evaluate(
                            "async()=> (await (await fetch('api/status')).json()).storage"
                        )
                    )
                    == root
                )

                def capture(name):
                    if screens:
                        page.screenshot(
                            path=OUT / ("ai-" + name + "-synthetic.png"),
                            animations="disabled",
                        )

                def ask(text):
                    page.get_by_role("textbox", name="提问或描述要办理的事项").fill(
                        text
                    )
                    page.get_by_role("button", name="发送", exact=True).click()
                    expect(page.get_by_role("button", name="停止回答")).to_have_count(
                        0, timeout=10000
                    )

                capture("desktop")
                ask("请分析昨天员工的测试话术。")
                page.locator(".ai-cite").first.click()
                audio = page.locator(".ai-ref-detail audio")
                expect(audio).to_be_visible()
                page.wait_for_function(
                    "document.querySelector('.ai-ref-detail audio')?.currentTime>=2.9"
                )
                capture("conversation")
                page.set_viewport_size(dict(width=390, height=844))
                if not page.locator(".ai-inspector").is_visible():
                    page.locator(".ai-cite").first.click()
                expect(page.get_by_role("button", name="收起证据").last).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                capture("mobile")
                page.get_by_role("button", name="收起证据").last.click()
                page.set_viewport_size(dict(width=1440, height=920))
                ask("请复核昨天的测试巡检记录。")
                page.locator(".ai-cite").last.click()
                expect(page.locator(".ai-ref-detail img")).to_be_visible()
                assert page.locator(".ai-ref-detail img").evaluate(
                    "img=>img.complete&&img.naturalWidth>0"
                )
                ask("把 TEST-01 的岗位改为前台")
                page.locator(".ai-cite").last.click()
                expect(page.locator(".ai-diff")).to_contain_text("前台")
                capture("device")
                assert (
                    server.badge_sync.directory.rows()["devices"][0]["position"]
                    == "前台"
                )
                page.get_by_role("button", name="新对话", exact=True).click()
                page.get_by_role("button", name="历史会话").click()
                page.locator(".ai-history>button").first.click()
                expect(page.locator(".ai-answer").last).to_contain_text("前台")
                browser.close()
            print(
                "PASS desktop, mobile, history, synthetic camera and audio evidence, device update"
            )
        finally:
            service.should_exit = True
            worker.join(15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", action="store_true")
    with tempfile.TemporaryDirectory() as tmp:
        verify(Path(tmp), parser.parse_args().screenshots)
