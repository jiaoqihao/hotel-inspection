"""Run one authenticated HTTPS store worker using the store's TLS certificate."""

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit
import uvicorn
from local.platform_db import database, migrate


def main():
    parser = argparse.ArgumentParser(description="启动门店 HTTPS 工作台")
    parser.add_argument(
        "--origin", required=True, help="员工浏览器实际访问的 HTTPS 地址"
    )
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--bind", required=True, help="门店主机的店内网卡地址")
    args = parser.parse_args()
    url = urlsplit(args.origin)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.path not in ("", "/")
        or url.query
        or url.fragment
        or url.username
    ):
        parser.error("请提供店内 HTTPS 源地址")
    if not args.cert.is_file() or not args.key.is_file():
        parser.error("请检查证书及私钥路径")
    migrate()
    with database() as db:
        if not db.execute("SELECT 1 FROM users WHERE role='管理员'").fetchone():
            parser.error("请先用 python -m local.access 创建管理员")
    os.environ["HOTEL_STORE_ORIGIN"] = args.origin.rstrip("/")
    uvicorn.run(
        "local.server:app",
        host=args.bind,
        port=url.port or 443,
        workers=1,
        ssl_certfile=str(args.cert),
        ssl_keyfile=str(args.key),
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
