"""
种子矩阵相关路由：/seeds/status, /seeds/list, /seeds/{seed}/keywords
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_public_or_auth
from ..database import get_seeds_list, get_seed_keywords, get_seeds_status_snapshot

router = APIRouter(tags=["seeds"])


@router.get("/seeds/status")
def seeds_status(_user: Annotated[dict | None, Depends(verify_public_or_auth)]) -> dict:
    """返回种子矩阵与进化日志快照（公开只读，无需登录）。"""
    snap = get_seeds_status_snapshot()
    generated = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return {"generated_at": generated, **snap}


@router.get("/seeds/list")
def seeds_list(
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
    status: str | None = Query(
        default=None,
        description="筛选状态：active/pending/pruned，不传则返回全部",
    ),
    category: str | None = Query(
        default=None,
        description="筛选分类：pain_point/category_word/trend_word，不传则返回全部",
    ),
    page: int = Query(default=1, ge=1, description="页码"),
    limit: int = Query(default=50, ge=1, le=200, description="每页数量"),
) -> dict:
    """分页获取种子列表（公开只读，无需登录）。"""
    if status is not None and status not in ("active", "pending", "pruned"):
        raise HTTPException(
            status_code=400,
            detail="status 参数须为 active/pending/pruned 或不传",
        )
    if category is not None and category not in ("pain_point", "category_word", "trend_word"):
        raise HTTPException(
            status_code=400,
            detail="category 参数须为 pain_point/category_word/trend_word 或不传",
        )
    rows, total = get_seeds_list(status=status, page=page, limit=limit, category=category)
    total_pages = (total + limit - 1) // limit if limit > 0 else 0
    return {
        "items": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


@router.get("/seeds/{seed}/keywords")
def seed_keywords(
    seed: str,
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
    days: int = Query(default=30, ge=1, le=365, description="查询最近N天"),
    limit: int = Query(default=100, ge=1, le=200, description="返回数量"),
) -> dict:
    """获取指定种子关联的关键词列表（公开只读，无需登录）。"""
    if not seed or len(seed.strip()) == 0:
        raise HTTPException(status_code=400, detail="种子词不能为空")
    keywords = get_seed_keywords(seed, days=days, limit=limit)
    return {
        "seed": seed,
        "days": days,
        "total": len(keywords),
        "keywords": keywords,
    }
