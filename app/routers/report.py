"""
报告相关路由：/report/generate, /report/check, /report/latest, /report/history, /report/{report_id}
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import verify_api_key_or_cookie as verify_api_key, verify_public_or_auth
from ..database import get_latest_report, get_report_by_id, get_report_history
from ..report_engine import (
    get_current_keyword_snapshot,
    run_report_generation,
    should_generate_report,
)

router = APIRouter(tags=["report"])


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


class GenerateBody(BaseModel):
    force: bool = Field(default=True, description="忽略冷却期和阈值强制生成")


@router.post("/report/generate")
def report_generate(
    body: GenerateBody,
    _: Annotated[None, Depends(verify_api_key)],
) -> dict:
    """手动触发一次报告生成（需登录，同步执行）。"""
    try:
        result = run_report_generation(triggered_by="manual")
        if result.get("skipped"):
            raise HTTPException(
                status_code=409,
                detail="报告生成任务正在执行中，请稍后再试",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc
    return result


@router.get("/report/check")
def report_check(
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
) -> dict:
    """检查是否应该触发报告（公开只读，无需登录）。"""
    should, reason, detail = should_generate_report()

    cooldown_remaining = 0.0
    if reason == "cooldown":
        cooldown_remaining = detail.get("hours_remaining", 0)

    snapshot_detail = detail.get("snapshot")
    return {
        "should_generate": should,
        "reason": reason,
        "detail": {
            "new_gold": detail.get("new_gold", 0)
                if not snapshot_detail
                else len([
                    k for k in snapshot_detail.get("new_keywords", [])
                    if k.get("label") == "💎 金矿"
                ]),
            "score_delta": detail.get("score_delta", 0),
            "count_delta": detail.get("count_delta", 0),
            "cooldown_remaining_hours": round(cooldown_remaining, 1),
        },
    }


@router.get("/report/latest")
def report_latest(
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
) -> dict:
    """返回最新一份报告全文（公开只读，无需登录）。不存在时返回空壳（HTTP 200）。"""
    row = get_latest_report()
    if row is None:
        return {"id": None, "report_md": None}
    return {
        "id": row["id"],
        "report_md": row.get("report_md"),
        "triggered_by": row.get("triggered_by"),
        "keyword_count": row.get("keyword_count"),
        "new_gold_count": row.get("new_gold_count"),
        "score_delta_sum": row.get("score_delta_sum"),
        "prompt_version": row.get("prompt_version"),
        "created_at": _format_dt(row.get("created_at")),
    }


@router.get("/report/history")
def report_history_list(
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
    limit: int = Query(default=20, ge=1, le=50, description="返回条数，默认20，最大50"),
) -> dict:
    """返回历史报告列表（公开只读，无需登录，不含 report_md 全文）。"""
    rows = get_report_history(limit=limit)
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "triggered_by": r.get("triggered_by"),
            "keyword_count": r.get("keyword_count"),
            "new_gold_count": r.get("new_gold_count"),
            "score_delta_sum": r.get("score_delta_sum"),
            "prompt_version": r.get("prompt_version"),
            "created_at": _format_dt(r.get("created_at")),
        })
    return {"total": len(items), "items": items}


@router.get("/report/{report_id}")
def report_detail(
    report_id: int,
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
) -> dict:
    """返回指定报告全文（公开只读，无需登录），不存在返回 404。"""
    row = get_report_by_id(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    kw_json = row.get("keywords_json")
    if isinstance(kw_json, str):
        try:
            kw_json = json.loads(kw_json)
        except Exception:
            pass

    return {
        "id": row["id"],
        "report_md": row.get("report_md"),
        "triggered_by": row.get("triggered_by"),
        "keyword_count": row.get("keyword_count"),
        "new_gold_count": row.get("new_gold_count"),
        "score_delta_sum": row.get("score_delta_sum"),
        "keywords_json": kw_json,
        "prompt_version": row.get("prompt_version"),
        "created_at": _format_dt(row.get("created_at")),
    }