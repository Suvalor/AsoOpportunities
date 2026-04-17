"""
鉴权模块：
- verify_api_key：X-API-Key Header 鉴权（供 n8n 调用）
- get_current_user：JWT Cookie 鉴权（供浏览器页面调用）
- require_admin：管理员角色检查
- verify_api_key_or_cookie：双鉴权，任一有效即通过
"""

from __future__ import annotations

import hmac
import os

from fastapi import Cookie, Depends, Header, HTTPException

from . import user_auth


def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
    """校验 API 密钥；缺失或不匹配时返回 401。使用恒定时间比较防止时序攻击。"""
    expected = os.getenv("API_KEY", "")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API Key")


def get_current_user(
    access_token: str | None = Cookie(default=None),
) -> dict:
    """从 Cookie 中读取 JWT，解码后返回 user 信息。"""
    if not access_token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        return user_auth.decode_token(access_token)
    except Exception:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """仅 admin 角色通过。"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def verify_api_key_or_cookie(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    access_token: str | None = Cookie(default=None),
) -> None:
    """双鉴权：X-API-Key 或 JWT Cookie 任一有效即通过。使用恒定时间比较防止时序攻击。"""
    expected = os.getenv("API_KEY", "")
    if x_api_key and hmac.compare_digest(x_api_key, expected):
        return
    if access_token:
        try:
            user_auth.decode_token(access_token)
            return
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="未授权：需要有效的 API Key 或登录凭证")
