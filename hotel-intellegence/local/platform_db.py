"""Versioned store for phone calls, work orders and store accounts."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(
    os.environ.get("HOTEL_VOICE_DATA", Path(os.environ["LOCALAPPDATA"]) / "HotelVoice")
)
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / "platform.sqlite3"


@contextmanager
def database():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            yield conn
    finally:
        conn.close()


def migrate():
    with database() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            raise RuntimeError("门店数据版本高于本程序")
        if version == 0:
            sql = Path(__file__).with_name("migrations") / "001_platform.sql"
            db.executescript(sql.read_text(encoding="utf-8"))
        if version < 2:
            sql = Path(__file__).with_name("migrations") / "002_phone_analysis.sql"
            db.executescript(sql.read_text(encoding="utf-8"))
        if version < 3:
            sql = Path(__file__).with_name("migrations") / "003_phone_access.sql"
            db.executescript(sql.read_text(encoding="utf-8"))
        if version < 4:
            sql = Path(__file__).with_name("migrations") / "004_phone_desk.sql"
            db.executescript(sql.read_text(encoding="utf-8"))
