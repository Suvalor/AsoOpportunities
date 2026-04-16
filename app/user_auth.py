"""
用户认证：bcrypt 密码哈希 + JWT Token 生成/解码。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    raise ValueError(
        "环境变量 JWT_SECRET 未设置，请设置一个强密钥后重启。"
        "可使用：python -c \"import secrets; print(secrets.token_hex(32))\""
    )
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "72"))


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
