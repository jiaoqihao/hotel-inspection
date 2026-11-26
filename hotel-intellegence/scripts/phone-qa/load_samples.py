"""Deliver the generated QA corpus to the local 8765 test register."""
import argparse
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import shutil
import uuid
import requests


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("samples",type=Path)
    parser.add_argument("--date",type=date.fromisoformat,default=date.today())
    args=parser.parse_args()
    files=[args.samples/f"call-{n:02}.wav" for n in range(1,31)]
    if not all(f.is_file() for f in files):
        parser.error("请指定包含 call-01.wav 至 call-30.wav 的合成样本目录")
    base="http://127.0.0.1:8765"
    session=requests.Session()
    session.trust_env=False
    def get(path):
        response=session.get(base+path,timeout=5)
        response.raise_for_status()
        return response.json()
    session.headers["x-local-token"]=get("/api/status")["token"]
    inbox=Path(get("/api/phone/health")["test_inbox"])
    if not inbox.is_absolute() or inbox.name!="phone-test-inbox" or not inbox.is_dir():
        raise RuntimeError("请使用本机管理员的模拟录音接收目录")
    for i,path in enumerate(files,1):
        stamp=(datetime.combine(args.date,time(9))+timedelta(minutes=i*3)).isoformat()
        info=dict(caller="测试分机A",callee="测试前台",started=stamp)
        if i%2:
            with path.open("rb") as src:
                response=session.post(base+"/api/phone/calls",data={**info,"test":"true"},files={"file":(path.name,src)},timeout=30)
                response.raise_for_status()
        else:
            stem=f"qa-v1-{i:02}"
            out=inbox/(stem+".wav")
            temp=inbox/(uuid.uuid4().hex+".part")
            shutil.copyfile(path,temp)
            temp.replace(out)
            manifest={**info,"file":out.name,"bytes":out.stat().st_size,"call_id":stem,"source_id":"synthetic-qa"}
            temp.write_text(json.dumps(manifest,ensure_ascii=False),encoding="utf-8")
            temp.replace(inbox/(stem+".ready.json"))
    print("已交付 30 份测试样本。前台电话 → 通话记录 → 数据范围：测试。")


if __name__=="__main__":
    main()
