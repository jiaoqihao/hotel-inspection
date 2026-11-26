"""Exercise DeepSeek through badge uploads using three explicitly synthetic WAVs."""
import argparse
import os
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient


def verify(samples, screenshot):
    from local.server import app, TOKEN
    base = "http://127.0.0.1:8765"
    records = []
    with TestClient(app, base_url=base, headers={"x-local-token": TOKEN}) as client:
        for scene, source in (("会议", "挂牌"), ("销售沟通", "胸牌"), ("餐厅服务", "胸牌")):
            filename = scene + "-合成测试.wav"
            with (samples / filename).open("rb") as wav:
                response = client.post("/api/upload", files={"file": (filename, wav, "audio/wav")},
                                       data={"source": source, "test": "true", "diarization": "false"})
            response.raise_for_status()
            records.append((scene, response.json()["id"]))
        for scene, key in records:
            url = "/api/records/" + key
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                record = client.get(url).json()
                if record["status"] == "error":
                    raise AssertionError(record["error"])
                if record["status"] == "done":
                    break
                time.sleep(1)
            assert record["status"] == "done" and record["test"]
            report = record["report"]
            assert report["scene"] == scene and report["mode"] == "deepseek", report
            assert report["conclusions"] and report["todos"], report
            assert record["detectedScenes"] == [scene]
            assert report["sceneSource"] == "自动识别"
            assert report["coverage"][0]["scene"] == scene
            segments = {s["id"]: s for s in record["segments"]}
            for group in ("conclusions", "insights", "todos", "risks", "checks", "flow"):
                for row in report[group]:
                    assert row["evidence"]
                    for ev in row["evidence"]:
                        seg = segments[ev["segment"]]
                        assert ev["quote"] in seg["text"] and ev["start"] == seg["start"]
            assert client.get(url + "/audio").content.startswith(b"RIFF")
            task = client.patch(url + "/tasks/0", json={"done": True})
            assert task.json()["report"]["todos"][0]["done"]
            labels = {row["label"] for row in report["insights"]}
            if scene == "会议":
                assert {"已决定事项", "待确认事项"} <= labels, labels
            if scene == "销售沟通":
                assert {"顾虑", "竞品", "标签建议"} <= labels, labels
                person = client.post("/api/badge/customers", json={"name": "合成验收客户"}).json()
                client.patch(url + "/customer", json={"customerId": person["id"]}).raise_for_status()
                client.patch(url + "/findings/insights/0", json={"state": "确认"}).raise_for_status()
                assert client.get("/api/badge/customers").json()[0]["entries"][0]["recordId"] == key
            if scene == "餐厅服务":
                assert "情绪表达" in labels, labels
                idx = next(i for i, row in enumerate(report["risks"]) if row.get("complaint"))
                for state in ("确认", "已处理"):
                    client.patch(url + f"/findings/risks/{idx}", json={"state": state, "note": "合成验收反馈"}).raise_for_status()
            print("PASS", scene, report["analysisModel"], len(segments), "segments,", len(report["todos"]), "tasks", flush=True)
        if screenshot:
            from playwright.sync_api import sync_playwright, expect
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1080}, service_workers="block")
                # Serve the isolated application at the usual browser URL.
                def serve(route):
                    req = route.request
                    res = client.request(req.method, req.url, headers=req.headers, content=req.post_data_buffer)
                    route.fulfill(status=res.status_code, headers=dict(res.headers), body=res.content)
                page.route(base + "/**", serve)
                page.goto(base)
                page.get_by_role("button", name="挂牌与胸牌语音", exact=True).click()
                page.get_by_role("button", name="录音档案", exact=True).click()
                page.get_by_role("button", name="会议-合成测试.wav", exact=True).click()
                dlg = page.get_by_role("dialog", name="录音分析详情")
                expect(dlg.get_by_role("region", name="录音场景").get_by_role("heading", name="会议", exact=True)).to_be_visible()
                expect(dlg.get_by_text("DeepSeek 场景分析", exact=True)).to_be_visible()
                expect(dlg.get_by_role("heading", name="会议纪要")).to_be_visible()
                dlg.locator(".br-citations > summary").first.click()
                dlg.locator(".evidence").first.click()
                page.wait_for_function("()=>{const a=document.querySelector('audio');return a && !a.paused && a.currentTime>0.15}")
                assert dlg.locator("audio").evaluate("a=>a.duration") > 0
                dlg.locator("audio").evaluate("a=>a.pause()")
                dlg.locator(".br-citations > summary").first.click()
                dlg.get_by_text("更多操作", exact=True).click()
                with page.expect_download() as download:
                    dlg.get_by_role("button", name="导出报告", exact=True).click()
                html = Path(download.value.path()).read_text(encoding="utf8")
                assert "deepseek-flash" in html and "场景分析明细" in html and "行动待办" in html
                page.screenshot(path=str(screenshot))
                page.set_viewport_size({"width": 390, "height": 844})
                assert dlg.evaluate("e=>e.scrollWidth<=e.clientWidth+1")
                dlg.get_by_text("会议分析明细", exact=True).click()
                expect(dlg.get_by_text("行动待办", exact=True)).to_be_visible()
                page.screenshot(path=str(screenshot.with_name(screenshot.stem+"-mobile.png")))
                browser.close()
            print("PASS browser report and evidence playback", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path, help="目录内包含会议、销售沟通、餐厅服务的“场景-合成测试.wav”")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as root:
        os.environ["HOTEL_VOICE_DATA"] = root
        os.environ.setdefault("HOTEL_VOICE_MODELS", str(Path(os.environ["LOCALAPPDATA"]) / "HotelVoice/models"))
        verify(args.samples, args.screenshot)
