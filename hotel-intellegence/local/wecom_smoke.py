"""Browser acceptance at the standard local URL, backed by an isolated synthetic store."""

import argparse
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import wave
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
import uvicorn


def verify(directory, screenshot=False):
    os.environ["HOTEL_VOICE_DATA"] = directory
    from local import server
    from local.test_badge_wecom import DAY, TEST_HOOK, sample
    from local.badge_sync import Configuration

    base = "http://127.0.0.1:8765"

    def vendor(path, body, token=None):
        return {
            "/auth/token": {"accessToken": "test"},
            "/Device/Page": {"items": [dict(deviceNo="TEST-WECOM", deviceType="Bg4g", status="Offline")]},
            "/audio/list": [],
        }[path]

    fake_receipt = ("已发送", "", False)
    with (
        socket.socket() as sock,
        patch("local.badge_sync.vendor", vendor),
        patch("local.wecom_api.post_text", return_value=fake_receipt),
        patch("local.wecom_delivery.post_text", return_value=fake_receipt),
        patch("local.badge_daily.checked_text", side_effect=lambda v: v),
    ):
        sock.bind(("127.0.0.1", 0))
        upstream = f"http://127.0.0.1:{sock.getsockname()[1]}"
        service = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
        worker = threading.Thread(target=service.run, kwargs={"sockets": [sock]}, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if service.started:
                    break
                time.sleep(0.1)
            server.badge_sync.configure(Configuration(appId="QA", secret="fixture"))
            item = sample(
                filename="合成验收样本",
                createdAt=DAY + "T08:01",
                progress=100,
                size=0,
                sceneHint="餐厅服务",
                reviewed=False,
                duration=5,
                extension=".wav",
                scriptChecks={"1": [dict(segment=3, quote="欢迎光临", result="符合")]},
            )
            item["report"].update(checks=[], insights=[], summary="合成验收场景", scope="合成测试")
            for group in ("risks", "todos"):
                item["report"][group][0]["evidence"] = [dict(segment=3, start=2, quote="欢迎光临")]
            with wave.open(str(server.AUDIO / "synthetic.wav"), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", ""))
                wav.writeframes(bytes(16000 * 2 * 5))
            server.save(item)
            server.save(dict(item, id="test-excluded", filename="已标记测试录音", test=True))
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge")
                page = browser.new_page(viewport={"width": 1440, "height": 980})
                page.route(base + "/**", lambda r: r.continue_(url=r.request.url.replace(base, upstream, 1)))
                page.goto(base)
                assert Path(
                    page.evaluate("async()=> (await (await fetch('/api/status')).json()).storage")
                ) == Path(directory)

                def b(name):
                    return page.get_by_role("button", name=name, exact=True)

                b("挂牌与胸牌语音").click()
                b("TEST-WECOM").click()
                page.get_by_label("员工姓名", exact=True).fill("测试员工")
                page.get_by_label("绑定生效日期").fill(DAY)
                b("保存员工绑定").click()
                expect(page.get_by_text("绑定记录已保存")).to_be_visible()
                with server.connection() as db:
                    assert tuple(db.execute("SELECT since,employee FROM badge_wearers").fetchone()) == (
                        DAY,
                        "测试员工",
                    )
                b("关闭窗口").click()
                page.locator(".bd-nav summary").click()
                b("服务标准").click()
                b("新增话术").click()
                page.get_by_label("话术生效日期").fill(DAY)
                page.get_by_label("启用此话术").check()
                b("保存话术版本").click()
                expect(page.get_by_text("话术版本已保存")).to_be_visible()
                b("企微连接").click()
                page.get_by_label("群 Webhook").fill(TEST_HOOK)
                b("保存推送设置").click()
                b("发送测试消息").click()
                expect(page.get_by_text("企微已接收测试消息，请在目标群确认。")).to_be_visible()
                expect(page.get_by_text("1 段正式录音 · 已分析 1", exact=False)).to_be_visible()
                page.get_by_text("话术依据与人工复核", exact=False).click()
                page.locator(".wc-evidence summary").first.click()
                b("回听发言").click()
                expect(page.get_by_role("dialog", name="录音分析详情")).to_be_visible()
                page.wait_for_function("document.querySelector('audio')?.currentTime >= 2")
                b("关闭详情").click()
                page.get_by_label("复核结论").select_option("已确认偏差")
                b("保存复核").click()
                expect(page.get_by_text("迎宾问候 · 已确认偏差 1")).to_be_visible()
                b("预览群消息").click()
                expect(page.get_by_role("region", name="群消息预览")).to_contain_text("栖鉴 · 录音日报")
                b("发送该日日报").click()
                expect(page.get_by_text("已发送 · 已送达 1/1 条", exact=False)).to_be_visible(timeout=25000)
                page.get_by_label("每日自动发送").check()
                b("保存推送设置").click()
                expect(page.get_by_text("自动发送已开启")).to_be_visible()
                page.evaluate("window.scrollTo(0,0)")
                if screenshot:
                    path = Path(__file__).resolve().parents[1] / "guide" / "wecom-daily.png"
                    path.parent.mkdir(exist_ok=True)
                    page.screenshot(path=str(path))
                page.set_viewport_size({"width": 390, "height": 850})
                expect(b("企微连接")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                browser.close()
            print(
                "PASS device binding, versioned scripts, WeCom test, daily preview, review, mock delivery and mobile layout"
            )
        finally:
            service.should_exit = True
            worker.join(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", action="store_true")
    with tempfile.TemporaryDirectory() as temp:
        verify(temp, parser.parse_args().screenshot)
