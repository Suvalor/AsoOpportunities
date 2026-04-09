"""
ASO 蓝海扫描服务 — FastAPI 入口。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .auth import verify_api_key
from .database import (
    create_running_job,
    get_job,
    get_top_keywords,
    init_db,
    insert_keywords,
    update_job,
)
from .scanner import run_full_scan
from .scorer import blue_ocean_label, blue_ocean_score

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASO 蓝海关键词服务", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时初始化数据库表结构。"""
    init_db()
    logger.info("数据库 init_db 完成")


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _run_scan_background(batch_id: str, country: str) -> None:
    """后台线程：全量扫描 → 蓝海评分 → 入库 → 更新任务状态。"""
    try:
        results = run_full_scan(country=country)
        for r in results:
            score, flags = blue_ocean_score(r)
            r["blue_ocean_score"] = score
            r["blue_ocean_flags"] = flags
            r["blue_ocean_label"] = blue_ocean_label(score)
        insert_keywords(results, batch_id, country)
        update_job(batch_id, "done", total=len(results))
        logger.info("扫描任务完成 batch_id=%s 关键词数=%d", batch_id, len(results))
    except Exception as exc:
        logger.exception("扫描任务失败 batch_id=%s: %s", batch_id, exc)
        err = str(exc)[:60000]
        try:
            update_job(batch_id, "failed", error=err)
        except Exception as update_exc:
            logger.error("更新失败状态写入数据库异常: %s", update_exc)


class ScanStartBody(BaseModel):
    """触发扫描的请求体。"""

    country: str = Field(default="us", description="主市场区域代码")


@app.get("/health")
def health() -> dict:
    """健康检查（无需鉴权）。"""
    return {"status": "ok"}


@app.post("/scan/start")
def scan_start(
    body: ScanStartBody,
    _: Annotated[None, Depends(verify_api_key)],
) -> dict:
    """异步启动全量扫描，立即返回 batch_id。"""
    batch_id = str(uuid.uuid4())
    country = body.country.strip().lower() or "us"
    create_running_job(batch_id)
    thread = threading.Thread(
        target=_run_scan_background,
        args=(batch_id, country),
        daemon=True,
        name=f"aso-scan-{batch_id[:8]}",
    )
    thread.start()
    return {
        "batch_id": batch_id,
        "status": "started",
        "message": "扫描任务已启动，使用 batch_id 查询进度",
    }


@app.get("/scan/status/{batch_id}")
def scan_status(
    batch_id: str,
    _: Annotated[None, Depends(verify_api_key)],
) -> dict:
    """查询扫描任务状态。"""
    row = get_job(batch_id)
    if row is None:
        raise HTTPException(status_code=404, detail="batch_id 不存在")
    return {
        "batch_id": row["batch_id"],
        "status": row["status"],
        "total_keywords": row["total_keywords"],
        "created_at": _format_dt(row.get("created_at")),
        "finished_at": _format_dt(row.get("finished_at")),
        "error_msg": row.get("error_msg"),
    }


@app.get("/analysis/top")
def analysis_top(
    _: Annotated[None, Depends(verify_api_key)],
    label: str | None = None,
    limit: int = 50,
    days: int = 7,
) -> dict:
    """按时间窗口拉取高分关键词列表（供 n8n / AI 分析）。"""
    rows = get_top_keywords(label=label, limit=limit, days=days)
    generated = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    keywords: list[dict] = []
    for r in rows:
        keywords.append(
            {
                "keyword": r["keyword"],
                "blue_ocean_score": int(r["blue_ocean_score"] or 0),
                "blue_ocean_label": r.get("blue_ocean_label") or "",
                "blue_ocean_flags": r.get("blue_ocean_flags") or "",
                "top_reviews": r.get("top_reviews"),
                "concentration": float(r["concentration"])
                if r.get("concentration") is not None
                else None,
                "avg_update_age_months": float(r["avg_update_age_months"])
                if r.get("avg_update_age_months") is not None
                else None,
                "trend_gap": float(r["trend_gap"])
                if r.get("trend_gap") is not None
                else None,
                "rank_change": r.get("rank_change"),
                "scanned_at": _format_dt(r.get("scanned_at")),
            }
        )
    return {
        "generated_at": generated,
        "total": len(keywords),
        "keywords": keywords,
    }
