"""
分析相关路由：/analysis/top, /analysis/compare, /analysis/priors
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from aso_core.scorer import (
    _DECAY_RATE_DEFAULTS,
    _DIMENSION_DEFAULTS,
    PriorState,
)

from ..auth import verify_public_or_auth
from ..bayesian_updater import compute_credible_interval, get_current_priors
from ..database import get_compare_analysis, get_top_keywords

router = APIRouter(tags=["analysis"])

_ISO_A2 = re.compile(r"^[a-z]{2}$")


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _parse_countries_query(countries: str | None) -> list[str] | None:
    """解析 GET ?countries=us,gb 并校验 alpha-2。"""
    if countries is None or not str(countries).strip():
        return None
    parts = [p.strip().lower() for p in str(countries).split(",") if p.strip()]
    for p in parts:
        if not _ISO_A2.fullmatch(p):
            raise HTTPException(
                status_code=400,
                detail=f"countries 查询参数须为逗号分隔的 alpha-2 小写国家码，非法: {p!r}",
            )
    return parts


@router.get("/analysis/top")
def analysis_top(
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
    label: str | None = None,
    limit: int = 50,
    days: int = 7,
    countries: str | None = Query(
        default=None,
        description="可选，逗号分隔 alpha-2 小写，如 us,gb；不传则包含全部国家",
    ),
    cross_platform: bool | None = Query(
        default=None,
        description="可选，true 时仅返回 Google Play 也有补全的双平台词",
    ),
    trends_only: bool | None = Query(
        default=None,
        description="可选，true 时仅返回 Google Trends 上升的词",
    ),
) -> dict:
    """按时间窗口拉取高分关键词列表（供 n8n / AI 分析）。"""
    cc = _parse_countries_query(countries)
    rows = get_top_keywords(
        label=label, limit=limit, days=days, countries=cc,
        cross_platform=cross_platform, trends_only=trends_only,
    )
    generated = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    keywords: list[dict] = []
    for r in rows:
        keywords.append(
            {
                "keyword": r["keyword"],
                "country": (r.get("country") or "").strip().lower(),
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
                "gplay_autocomplete_rank": r.get("gplay_autocomplete_rank"),
                "gplay_top_reviews": int(r.get("gplay_top_reviews") or 0),
                "gplay_top_installs": str(r.get("gplay_top_installs") or "0"),
                "gplay_top_installs_num": int(r.get("gplay_top_installs_num") or 0),
                "gplay_avg_rating": float(r.get("gplay_avg_rating") or 0),
                "cross_platform": bool(r.get("cross_platform")),
                "trends_rising": bool(r.get("trends_rising")),
                "trends_rising_count": int(r.get("trends_rising_count") or 0),
                "reddit_post_count": int(r.get("reddit_post_count") or 0),
                "reddit_avg_score": float(r.get("reddit_avg_score") or 0),
            }
        )
    return {
        "generated_at": generated,
        "total": len(keywords),
        "keywords": keywords,
    }


@router.get("/analysis/compare")
def analysis_compare(
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
    days_recent: int = 7,
    days_baseline: int = 14,
) -> dict:
    """
    对比本期（最近 days_recent 天）与基线窗口（此前连续 days_baseline 天）内各词最新快照。
    score_delta 由 MySQL LAG 与两期 UNION 推导，见 database.get_compare_analysis。
    """
    dr = min(max(int(days_recent), 1), 366)
    db = min(max(int(days_baseline), 1), 366)
    buckets = get_compare_analysis(days_recent=dr, days_baseline=db)
    generated = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return {
        "generated_at": generated,
        "days_recent": dr,
        "days_baseline": db,
        "counts": {
            "rising": len(buckets["rising"]),
            "new_entries": len(buckets["new_entries"]),
            "sustained": len(buckets["sustained"]),
            "dropping": len(buckets["dropping"]),
        },
        "rising": buckets["rising"],
        "new_entries": buckets["new_entries"],
        "sustained": buckets["sustained"],
        "dropping": buckets["dropping"],
    }


@router.get("/analysis/priors")
def analysis_priors(
    _user: Annotated[dict | None, Depends(verify_public_or_auth)],
) -> dict:
    """返回当前贝叶斯先验/后验状态，含可信区间和与原始硬编码权重的对比。"""
    priors = get_current_priors()
    generated = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    dimensions: list[dict] = []
    for dim in sorted(priors.keys()):
        p = priors[dim]
        ci_lower, ci_upper = compute_credible_interval(priors, dim, ci=0.95)

        entry: dict = {
            "dimension": dim,
            "n_obs": p.n_obs,
            "ci_lower": ci_lower,
            "ci_upper": ci_upper,
        }

        if dim in _DIMENSION_DEFAULTS:
            default_weight, max_val = _DIMENSION_DEFAULTS[dim]
            posterior_mean = p.alpha / (p.alpha + p.beta_param) * max_val
            entry["type"] = "weight"
            entry["default_weight"] = default_weight
            entry["posterior_mean"] = round(posterior_mean, 2)
            entry["max_value"] = max_val
            entry["alpha"] = round(p.alpha, 2)
            entry["beta"] = round(p.beta_param, 2)
        elif dim in _DECAY_RATE_DEFAULTS:
            default_rate, default_var = _DECAY_RATE_DEFAULTS[dim]
            entry["type"] = "decay_rate"
            entry["default_rate"] = default_rate
            entry["posterior_mean"] = round(p.mu, 6)
            entry["sigma_sq"] = round(p.sigma_sq, 8)

        dimensions.append(entry)

    return {
        "generated_at": generated,
        "total_dimensions": len(dimensions),
        "dimensions": dimensions,
    }
