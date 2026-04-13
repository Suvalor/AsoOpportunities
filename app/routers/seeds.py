"""
种子矩阵相关路由：/seeds/status
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth import verify_api_key_or_cookie as verify_api_key
from ..database import get_seeds_status_snapshot

router = APIRouter(tags=["seeds"])


@router.get("/seeds/status")
def seeds_status(_: Annotated[None, Depends(verify_api_key)]) -> dict:
    """返回种子矩阵与进化日志快照（需鉴权）。"""
    snap = get_seeds_status_snapshot()
    generated = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return {"generated_at": generated, **snap}
