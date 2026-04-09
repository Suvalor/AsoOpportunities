"""
API 密钥鉴权：通过请求头 X-API-Key 与环境变量 API_KEY 比对。
"""

import os

from fastapi import Header, HTTPException


def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
    """校验 API 密钥；缺失或不匹配时返回 401。"""
    expected = os.getenv("API_KEY", "")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API Key")
