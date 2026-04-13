"""
扫描相关路由：/scan/start, /scan/status/{batch_id}
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aso_core.scanner import run_full_scan
from aso_core.scorer import blue_ocean_label, blue_ocean_score

from ..auth import verify_api_key
from ..database import (
    create_running_job,
    get_active_seeds,
    get_job,
    get_tracking_scan_seeds,
    insert_keywords,
    update_job,
)
from ..evolution import run_evolution_after_full_scan

logger = logging.getLogger(__name__)

TRACKING_MIN_BLUE_SCORE = 60
TRACKING_SEED_LOOKBACK_DAYS = 30

_ISO_A2 = re.compile(r"^[a-z]{2}$")

router = APIRouter(tags=["scan"])


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _normalize_scan_countries(raw: list[str] | None) -> list[str] | None:
    """
    校验 POST /scan/start 的 countries：ISO 3166-1 alpha-2 小写，最多 5 国。
    非法或超限抛出 HTTPException(400)。
    """
    if raw is None:
        return None
    if len(raw) > 5:
        raise HTTPException(status_code=400, detail="最多同时扫描 5 个国家")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        cc = str(item).strip().lower()
        if not _ISO_A2.fullmatch(cc):
            raise HTTPException(
                status_code=400,
                detail=(
                    "countries 中每项须为 ISO 3166-1 alpha-2 小写（如 us、gb），"
                    f"非法值: {item!r}"
                ),
            )
        if cc not in seen:
            seen.add(cc)
            out.append(cc)
    return out


def _run_scan_background(
    batch_id: str,
    countries: list[str] | None,
    mode: Literal["full", "tracking"],
) -> None:
    """后台线程：扫描（full / tracking）→ 蓝海评分 → 入库 → 更新任务状态。"""
    try:
        if mode == "tracking":
            seeds = get_tracking_scan_seeds(
                TRACKING_MIN_BLUE_SCORE, days=TRACKING_SEED_LOOKBACK_DAYS
            )
            if not seeds:
                logger.info(
                    "tracking 模式：无符合条件的 active 种子 batch_id=%s",
                    batch_id,
                )
                insert_keywords([], batch_id, "us")
                update_job(batch_id, "done", total=0)
                return
            results = run_full_scan(
                countries=countries,
                seeds=seeds,
                mode="tracking",
            )
        else:
            seeds = get_active_seeds()
            if not seeds:
                logger.warning(
                    "full 模式：aso_seeds 无 active 种子，batch_id=%s",
                    batch_id,
                )
            results = run_full_scan(
                countries=countries,
                seeds=seeds,
                mode="full",
            )
        for r in results:
            score, flags = blue_ocean_score(r)
            r["blue_ocean_score"] = score
            r["blue_ocean_flags"] = flags
            r["blue_ocean_label"] = blue_ocean_label(score)
        insert_keywords(results, batch_id, "us")
        if mode == "full":
            run_evolution_after_full_scan(batch_id)

        try:
            from ..report_engine import run_report_generation, should_generate_report

            should, reason, detail = should_generate_report()
            if should or mode == "full":
                trigger = "weekly_full" if mode == "full" else "auto_threshold"
                run_report_generation(triggered_by=trigger)
                logger.info("[Report] 报告已生成，触发原因: %s", reason)
            else:
                logger.info("[Report] 未触发报告生成，原因: %s，详情: %s", reason, detail)
        except Exception as report_exc:
            logger.warning("[Report] 报告生成失败（不影响扫描结果）: %s", report_exc)

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

    countries: list[str] | None = Field(
        default=None,
        description="可选；不传则使用环境变量 ASO_SCAN_COUNTRIES；每项为 alpha-2 小写，最多 5 个",
    )
    mode: Literal["full", "tracking"] = Field(
        default="full",
        description=(
            "full=从 aso_seeds active 取种子做矩阵扫描并在写库后执行进化；"
            "tracking=仅对近30天曾出现蓝海分≥60 的 active 种子重复展开扫描"
        ),
    )


@router.post("/scan/start")
def scan_start(
    body: ScanStartBody,
    _: Annotated[None, Depends(verify_api_key)],
) -> dict:
    """异步启动扫描（全量或追踪），立即返回 batch_id。"""
    batch_id = str(uuid.uuid4())
    mode = body.mode
    raw_cc = body.countries
    if raw_cc is not None and len(raw_cc) == 0:
        raw_cc = None
    countries = _normalize_scan_countries(raw_cc)
    create_running_job(batch_id)
    thread = threading.Thread(
        target=_run_scan_background,
        args=(batch_id, countries, mode),
        daemon=True,
        name=f"aso-scan-{batch_id[:8]}",
    )
    thread.start()
    return {
        "batch_id": batch_id,
        "status": "started",
        "mode": mode,
        "countries": countries,
        "message": "扫描任务已启动，使用 batch_id 查询进度",
    }


@router.get("/scan/status/{batch_id}")
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
