"""Exercise badge management and responsive archive filters against an isolated test account."""
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import uvicorn
from playwright.sync_api import expect, sync_playwright


def verify(root):
    os.environ["HOTEL_VOICE_DATA"] = root
    from local import server
    from local.badge_sync import Configuration

    numbers = ["QA-H1", "QA-H2", "QA-B1", "QA-B2"]
    catalog = [
        dict(deviceNo=n, deviceType="Hanging4G" if "-H" in n else "Bg4g",
             status="Offline" if n.endswith("2") else "Online")
        for n in numbers
    ]

    def vendor(path, body, token=None):
        return {"/auth/token": {"accessToken": "test"}, "/Device/Page": {"items": catalog},
                "/audio/list": []}[path]

    base = "http://127.0.0.1:8765"
    with socket.socket() as sock, patch("local.badge_sync.vendor", vendor):
        sock.bind(("127.0.0.1", 0))
        upstream = f"http://127.0.0.1:{sock.getsockname()[1]}"
        service = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
        worker = threading.Thread(target=service.run, kwargs={"sockets": [sock]}, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if service.started:
                    break
                time.sleep(.1)
            assert service.started
            server.badge_sync.configure(Configuration(appId="QA", secret="fixture"))
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge")
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.route(base + "/**", lambda r: r.continue_(url=r.request.url.replace(base, upstream, 1)))
                page.goto(base)
                assert Path(page.evaluate("async()=> (await (await fetch('/api/status')).json()).storage")) == Path(root)
                button = lambda name: page.get_by_role("button", name=name, exact=True)
                button("挂牌与胸牌语音").click()
                expect(page.locator(".bd-panel tbody tr")).to_have_count(4)
                button("手动添加").click()
                button("手动录入").click()
                page.get_by_label("设备编号").fill("QA-MANUAL")
                button("加入待添加清单").click()
                button("添加 1 台").click()
                expect(page.locator(".bd-panel tbody tr")).to_have_count(5)
                expect(page.locator(".bd-在线")).to_have_count(2)
                page.get_by_label("在线状态").select_option("离线")
                expect(page.locator(".bd-panel tbody tr")).to_have_count(2)
                page.get_by_label("在线状态").select_option("全部状态")
                button("QA-H1").click()
                page.get_by_text("管理设备", exact=True).click()
                button("编辑设备").click()
                page.get_by_label("设备名称").fill("测试前台一号")
                button("保存修改").click()
                button("测试前台一号").click()
                button("查看这台设备的录音").click()
                expect(page.locator(".vf-select")).to_have_text("测试前台一号")
                expect(page.get_by_label("数据范围")).to_have_count(0)
                button("改进待办").click()
                expect(page.locator(".vf-select")).to_have_text("测试前台一号")
                page.locator(".vf-select").click()
                page.get_by_role("searchbox").fill("QA-H2")
                button("QA-H2").click()
                expect(page.locator(".vf-select")).to_contain_text("QA-H2")
                button("胸牌").click()
                expect(page.locator(".vf-select")).to_contain_text("所有设备")
                for width in (1440, 1024, 768, 390):
                    page.set_viewport_size({"width": width, "height": 850})
                    boxes = [page.locator(s).bounding_box() for s in (".vf-segments", ".vf-select")]
                    assert all(round(b["height"]) == 40 for b in boxes)
                    if width >= 1024:
                        assert abs(boxes[0]["y"] - boxes[1]["y"]) < 1
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                browser.close()
            print("PASS automatic discovery, multi-device add/edit, presence, device archives and responsive filters")
        finally:
            service.should_exit = True
            worker.join(timeout=20)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        verify(directory)

