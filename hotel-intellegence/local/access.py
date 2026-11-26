"""Store mode uses same-origin HTTPS and expiring per-user sessions."""

import argparse
import getpass
import hashlib
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from local.platform_db import database, migrate

ORIGIN = os.environ.get("HOTEL_STORE_ORIGIN", "").rstrip("/")
STORE = bool(ORIGIN)
if STORE and (
    not ORIGIN.startswith("https://")
    or urlsplit(ORIGIN).path
    or not urlsplit(ORIGIN).hostname
):
    raise RuntimeError("HOTEL_STORE_ORIGIN 需要完整店内 HTTPS 源地址")
ROLES = {"管理员", "经理", "前台", "质检员", "部门员工"}
SUPERVISORS = {"管理员", "经理"}
PHONE = SUPERVISORS | {"前台"}
QUALITY = SUPERVISORS | {"质检员"}
router = APIRouter(prefix="/api/session")


def digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


def set_user(name, password, role, department, grants=None):
    if (
        not name.strip()
        or role not in ROLES
        or len(password) < 12
        or (role == "部门员工" and not department.strip())
    ):
        raise ValueError("名称、角色、部门无效或密码少于 12 个字符")
    salt = secrets.token_bytes(16)
    with database() as db:
        db.execute(
            """INSERT INTO users VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
               salt=excluded.salt,digest=excluded.digest,role=excluded.role,department=excluded.department""",
            (name.strip(), salt, digest(password, salt), role, department.strip()),
        )
        db.execute("DELETE FROM phone_permissions WHERE name=?", (name.strip(),))
        if grants is not None:
            db.execute(
                "INSERT INTO phone_permissions VALUES(?,?,?,?,?)",
                (name.strip(), *grants),
            )
        db.execute("DELETE FROM sessions WHERE name=?", (name.strip(),))


def phone_permissions(role, row=None):
    defaults = dict(
        quality=False,
        audio=role in PHONE,
        numbers=role in SUPERVISORS,
        export=role in SUPERVISORS,
    )
    return {k: bool(row[k]) if row else v for k, v in defaults.items()}


def identity(request):
    if not STORE:
        return {
            "name": "本机管理员",
            "role": "管理员",
            "department": "",
            "phone": phone_permissions("管理员"),
        }
    token = hashlib.sha256(
        request.cookies.get("hotel_session", "").encode()
    ).hexdigest()
    with database() as db:
        row = db.execute(
            "SELECT u.name,u.role,u.department FROM users u JOIN sessions s ON s.name=u.name WHERE token=? AND expires>?",
            (token, time.time()),
        ).fetchone()
        if not row:
            return None
        grants = db.execute(
            "SELECT * FROM phone_permissions WHERE name=?", (row["name"],)
        ).fetchone()
        return {**dict(row), "phone": phone_permissions(row["role"], grants)}


def allowed(user, path, method):
    role = user["role"]
    if path in (
        "/api/status",
        "/api/overview",
        "/api/reports",
        "/api/session",
        "/api/session/logout",
        "/api/ping",
    ):
        return True
    if path.startswith("/api/badge/devices"):
        if method == "GET" and path != "/api/badge/devices/catalog":
            return role in QUALITY
        return role == "管理员"
    if path.startswith("/api/badge/sync"):
        return role == "管理员"
    if path.startswith("/api/phone/orders"):
        return role in PHONE or role == "部门员工"
    if path.startswith("/api/phone/"):
        grants = user.get("phone") or phone_permissions(role)
        permitted = role in PHONE or (role == "质检员" and grants["quality"])
        if path.endswith("/export"):
            return permitted and grants["export"]
        if path.endswith("/audio"):
            return permitted and grants["audio"]
        if path.startswith("/api/phone/ledger"):
            return role=="管理员"
        if path=="/api/phone/duty" and method=="PUT":
            return role=="管理员"
        if path.startswith("/api/phone/duty"):
            return role in PHONE
        if path.endswith("/reconcile"):
            return role == "管理员"
        if role == "质检员" and permitted:
            return (
                method == "GET"
                and (
                    path.startswith("/api/phone/calls/")
                    or path in ("/api/phone/register", "/api/phone/health")
                )
            ) or (
                path.startswith("/api/phone/calls/")
                and path.rsplit("/", 1)[-1]
                in ("review", "read", "favorite", "reanalyze")
            )
        if path.endswith("/extensions") and method != "GET":
            return role == "管理员"
        return role in PHONE
    if path.startswith("/api/patrol") and method != "GET":
        return role in SUPERVISORS
    if path.startswith("/api/records/") and path.endswith("/metadata"):
        return role in SUPERVISORS
    if path.endswith("/export") or method == "DELETE":
        return role in SUPERVISORS
    if path.startswith(("/api/badge/", "/api/records", "/api/upload", "/api/vision", "/api/patrol")):
        return role in QUALITY
    return False


class Login(BaseModel):
    name: str = Field(max_length=100)
    password: str = Field(max_length=512)


@router.get("")
def session(request: Request):
    return {"store": STORE, "user": identity(request)}


@router.post("/login")
def login(body: Login, response: Response):
    if not STORE:
        raise HTTPException(400, "仅在门店模式登录")
    with database() as db:
        user = db.execute("SELECT * FROM users WHERE name=?", (body.name,)).fetchone()
        candidate = digest(body.password, user["salt"] if user else bytes(16))
        if not user or not secrets.compare_digest(candidate, user["digest"]):
            raise HTTPException(401, "账号或密码错误")
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                body.name,
                time.time() + 28800,
            ),
        )
    response.set_cookie(
        "hotel_session",
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=28800,
    )
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    key = hashlib.sha256(request.cookies.get("hotel_session", "").encode()).hexdigest()
    with database() as db:
        db.execute("DELETE FROM sessions WHERE token=?", (key,))
    response.delete_cookie(
        "hotel_session", secure=True, httponly=True, samesite="strict"
    )
    return {"ok": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="创建门店账号或重设密码")
    parser.add_argument("name")
    parser.add_argument("role", choices=sorted(ROLES))
    parser.add_argument("--department", default="")
    for flag in ("quality", "audio", "numbers", "export"):
        parser.add_argument(
            "--phone-" + flag, action=argparse.BooleanOptionalAction, default=None
        )
    args = parser.parse_args()
    migrate()
    defaults = phone_permissions(args.role)
    grants = [
        (
            defaults[k]
            if getattr(args, "phone_" + k) is None
            else getattr(args, "phone_" + k)
        )
        for k in defaults
    ]
    set_user(
        args.name,
        getpass.getpass("密码（至少12位）: "),
        args.role,
        args.department,
        grants,
    )
