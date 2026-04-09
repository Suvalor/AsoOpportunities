"""
封装 iTunes Search API，评估关键词竞争度并计算机会分。
"""

from __future__ import annotations

import math
import time
import logging
from datetime import datetime, timezone

import requests

from .settings import get_settings

logger = logging.getLogger(__name__)

ITUNES_URL = "https://itunes.apple.com/search"


def _parse_update_age_months(date_str: str) -> int:
    """将 currentVersionReleaseDate 转为距今整月数；失败返回 99。"""
    try:
        release_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        days = (today - release_date).days
        return max(int(days / 30), 0)
    except Exception:
        return 99


def get_competition(
    keyword: str,
    country: str | None = None,
    limit: int | None = None,
    sleep: float | None = None,
) -> dict:
    """查询 iTunes Search API，返回该关键词的竞争数据。"""
    s = get_settings()
    if country is None:
        country = s.default_country
    if limit is None:
        limit = s.itunes_limit
    if sleep is None:
        sleep = s.rate_limit_sleep

    params = {
        "term": keyword,
        "entity": "software",
        "limit": limit,
        "country": country,
    }

    empty_result = {
        "count": 0,
        "avg_rating": 0.0,
        "avg_reviews": 0,
        "top_reviews": 0,
        "top_current_reviews": 0,
        "avg_update_age_months": 99,
        "concentration": 0.0,
    }

    try:
        response = requests.get(ITUNES_URL, params=params, timeout=10)
        response.raise_for_status()
        apps = response.json().get("results", [])
    except requests.RequestException as exc:
        logger.warning("iTunes 请求失败 [%s]: %s", keyword, exc)
        return empty_result
    except ValueError as exc:
        logger.warning("iTunes 解析 JSON 失败 [%s]: %s", keyword, exc)
        return empty_result
    finally:
        time.sleep(sleep)

    if not apps:
        return empty_result

    ratings = [a.get("averageUserRating", 0.0) for a in apps]
    review_counts = [a.get("userRatingCount", 0) for a in apps]

    top_current_reviews = apps[0].get("userRatingCountForCurrentVersion", 0)

    age_list = [
        _parse_update_age_months(a["currentVersionReleaseDate"])
        for a in apps
        if "currentVersionReleaseDate" in a
    ]
    age_list += [99] * (len(apps) - len(age_list))
    avg_update_age_months = int(sum(age_list) / len(age_list)) if age_list else 99

    top5_counts = review_counts[:5]
    top5_sum = sum(top5_counts)
    concentration = round(review_counts[0] / top5_sum, 2) if top5_sum > 0 else 0.0

    return {
        "count": len(apps),
        "avg_rating": round(sum(ratings) / len(ratings), 2),
        "avg_reviews": int(sum(review_counts) / len(review_counts)),
        "top_reviews": review_counts[0],
        "top_current_reviews": top_current_reviews,
        "avg_update_age_months": avg_update_age_months,
        "concentration": concentration,
    }


def opportunity_score(autocomplete_rank: int, competition: dict) -> float:
    """计算关键词机会分（float，保留两位小数）。"""
    volume_proxy = 20 - autocomplete_rank
    if volume_proxy <= 0:
        return 0.0

    top_reviews = max(competition.get("top_reviews", 0), 0)
    competition_strength = math.log10(top_reviews + 1) if top_reviews > 0 else 1.0

    if competition_strength == 0:
        return round(float(volume_proxy) * 10, 2)

    return round(volume_proxy / competition_strength, 2)
