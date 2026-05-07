"""
用户认证路由：/auth/register, /auth/login, /auth/logout, /auth/me
"""

from __future__ import annotations

import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..database import create_user, get_user_by_username, get_user_count, update_last_login
from ..user_auth import create_token, hash_password, verify_password, JWT_EXPIRE_HOURS

router = APIRouter(tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


class AuthBody(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=64)


@router.post("/auth/register")
def auth_register(body: AuthBody) -> dict:
    user_count = get_user_count()

    allow_register = os.getenv("ALLOW_REGISTER", "false").lower() == "true"
    if not allow_register and user_count > 0:
        raise HTTPException(status_code=403, detail="注册已关闭")

    if not _USERNAME_RE.fullmatch(body.username):
        raise HTTPException(
            status_code=400,
            detail="用户名只允许3-32位字母、数字、下划线",
        )

    role = "admin" if user_count == 0 else "viewer"
    pw_hash = hash_password(body.password)

    try:
        new_id = create_user(body.username, pw_hash, role)
    except Exception as exc:
        if "Duplicate" in str(exc):
            raise HTTPException(status_code=409, detail="用户名已存在")
        raise HTTPException(status_code=500, detail="注册失败，请稍后重试") from exc

    return {"id": new_id, "username": body.username, "role": role}


@router.post("/auth/login")
def auth_login(body: AuthBody) -> JSONResponse:
    user = get_user_by_username(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_token(user["id"], user["username"], user["role"])
    update_last_login(user["id"])

    response = JSONResponse(content={
        "username": user["username"],
        "role": user["role"],
    })
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        path="/",
        max_age=JWT_EXPIRE_HOURS * 3600,
    )
    return response


@router.post("/auth/logout")
def auth_logout() -> JSONResponse:
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key="access_token", path="/")
    return response


@router.get("/auth/register-status")
def auth_register_status() -> dict:
    """返回当前是否允许注册（无需鉴权）。"""
    allow_env = os.getenv("ALLOW_REGISTER", "false").lower() == "true"
    if allow_env:
        return {"allow_register": True}
    return {"allow_register": get_user_count() == 0}


@router.get("/auth/me")
def auth_me(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return {
        "user_id": user.get("sub"),
        "username": user.get("username"),
        "role": user.get("role"),
    }
