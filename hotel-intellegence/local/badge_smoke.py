"""Browser regression for the short greeting test MP3. Uses an isolated ASGI server."""

import argparse
import json, os, tempfile, threading, time, socket
from pathlib import Path
import uvicorn
from playwright.sync_api import sync_playwright, expect


def main(root, sample, screenshot):
    os.environ["HOTEL_VOICE_DATA"] = root
    os.environ["HOTEL_VOICE_MODELS"] = str(
        Path(os.environ["LOCALAPPDATA"]) / "HotelVoice/models"
    )
    from local import server

    base = "http://127.0.0.1:8765"
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    upstream = f"http://127.0.0.1:{sock.getsockname()[1]}"
    service = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
    worker = threading.Thread(
        target=service.run, kwargs={"sockets": [sock]}, daemon=True
    )
    worker.start()
    for _ in range(100):
        if service.started:
            break
        time.sleep(0.1)
    assert service.started
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 1000}, service_workers="block"
            )
            ctx.route(
                base + "/**",
                lambda route: route.continue_(
                    url=route.request.url.replace(base, upstream, 1)
                ),
            )
            page = ctx.new_page()
            page.goto(base)
            storage = page.evaluate(
                "async()=> (await (await fetch('/api/status')).json()).storage"
            )
            assert Path(storage) == Path(root), storage
            page.get_by_role("button", name="挂牌与胸牌语音", exact=True).click()
            page.get_by_role("button", name="录音档案", exact=True).click()
            page.get_by_role("button", name="上传录音", exact=True).click()
            # This fixture checks the ordinary transcription baseline, even when speaker models are installed.
            speakers = page.get_by_role("checkbox", name="按声纹区分说话人")
            if speakers.is_checked():
                speakers.uncheck()
            for source in ("胸牌", "挂牌"):
                page.get_by_label("设备来源").select_option(source)
                page.get_by_label("设备编号").fill("QA-01")
                page.get_by_label("实际录制时间").fill("2026-11-25T17:26")
                page.get_by_label("员工姓名或编号").fill("设备测试")
                page.locator("input[type=file]").set_input_files(
                    dict(
                        name="QA-" + source + ".mp3",
                        mimeType="audio/mpeg",
                        buffer=sample.read_bytes(),
                    )
                )
                page.get_by_role("button", name="上传并分析", exact=True).click()
                dlg = page.get_by_role("dialog", name="录音分析详情")
                expect(dlg.get_by_role("button", name="确认已复核")).to_be_visible(
                    timeout=300000
                )
                expect(dlg.get_by_text("缺少可分析片段", exact=True)).to_be_visible()
                assert dlg.locator("audio").evaluate("a=>a.duration") > 9
                dlg.get_by_role("button", name="确认已复核").click()
                expect(dlg.get_by_role("button", name="撤销复核")).to_be_visible()
                for button, ext in [("导出 JSON", "json"), ("导出报告", "html")]:
                    dlg.get_by_text("更多操作", exact=True).click()
                    with page.expect_download() as d:
                        dlg.get_by_role("button", name=button, exact=True).click()
                    blob = Path(d.value.path()).read_text(encoding="utf8")
                    if ext == "json":
                        item = json.loads(blob)
                        assert (
                            not item["test"]
                            and item["source"] == source
                            and item["reviewed"]
                        )
                        assert (
                            item["report"]["scene"] is None
                            and "segments" not in item
                        ), (item["report"]["scene"], item["report"]["batchCount"], item.get("sceneHint"), item["duration"])
                        assert (
                            not item["report"]["risks"] and not item["report"]["todos"]
                        ), item["report"]
                        print(
                            "PASS",
                            source,
                            item["report"]["mode"],
                            item["duration"],
                            item["report"]["batchCount"],
                            flush=True,
                        )
                    else:
                        assert source in blob
                dlg.get_by_role("button", name="关闭详情").click()
            page.get_by_role("button", name="录音档案", exact=True).click()
            expect(page.get_by_label("数据范围")).to_have_count(0)
            expect(page.get_by_text("共 2 条")).to_be_visible()
            # Both uploaded recordings reference QA-01, but this account has no registered devices.
            page.locator(".vf-select").click()
            expect(page.locator(".vf-options button")).to_have_count(1)
            expect(page.locator(".vf-options button")).to_have_text("所有设备")
            page.keyboard.press("Escape")
            devices = page.get_by_role("group", name="按设备类型筛选")
            for device in ("挂牌", "胸牌"):
                devices.get_by_role("button", name=device, exact=True).click()
                expect(page.get_by_text("共 1 条")).to_be_visible()
                expect(page.get_by_role("button", name="QA-" + device + ".mp3", exact=True)).to_be_visible()
            devices.get_by_role("button", name="全部设备").click()
            expect(page.get_by_text("共 2 条")).to_be_visible()
            page.get_by_role("button", name="管理 QA-胸牌.mp3").click()
            dlg=page.get_by_role("dialog",name="管理录音")
            dlg.get_by_label("录音名称").fill("测试问候录音")
            dlg.get_by_label("备注").fill("临时库合成验收")
            dlg.get_by_role("button",name="保存修改").click()
            page.get_by_role("button",name="管理 测试问候录音").click()
            dlg.get_by_role("button",name="删除录音").click()
            page.get_by_role("alertdialog").get_by_role("button",name="确认删除").click()
            expect(page.get_by_text("共 1 条")).to_be_visible()
            if screenshot:
                page.screenshot(path=screenshot)
            print("PASS browser filters, HTML, JSON, actual playback WAV", flush=True)
            browser.close()
    finally:
        service.should_exit = True
        worker.join(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as root:
        main(root, args.sample, args.screenshot)
