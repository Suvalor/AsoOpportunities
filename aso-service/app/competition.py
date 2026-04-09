"""
封装 iTunes Search API，评估关键词竞争度并计算机会分。

竞争度模型：
  - 用搜索该词返回的头部 App 的评论数量代表"守门人强度"
  - 用 log10 压缩，避免 10 万评论的头部 App 把分数压至接近 0
  - volume_proxy = 20 - autocomplete_rank（rank=1 → 19分，rank=20 → 0分）
  - opportunity_score = volume_proxy / log10(top_reviews + 1)
"""

import math
import time
import logging
from datetime import datetime, timezone

import requests

from .config import COUNTRY, ITUNES_LIMIT, RATE_LIMIT_SLEEP

logger = logging.getLogger(__name__)

ITUNES_URL = "https://itunes.apple.com/search"


def _parse_update_age_months(date_str: str) -> int:
    """
    将 iTunes API 的 currentVersionReleaseDate 字段转换为距今月数。
    格式为 "2023-10-15T07:45:10Z"，取前10字符解析日期部分。
    解析失败时返回 99（视为竞品长期未更新）。
    """
    try:
        release_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        days = (today - release_date).days
        return max(int(days / 30), 0)
    except Exception:
        return 99


def get_competition(
    keyword: str,
    country: str = COUNTRY,
    limit: int = ITUNES_LIMIT,
    sleep: float = RATE_LIMIT_SLEEP,
) -> dict:
    """
    查询 iTunes Search API，返回该关键词的竞争数据。

    参数:
        keyword: 目标关键词
        country: 市场区域代码
        limit:   取前多少名 App 作为竞争样本
        sleep:   请求后等待秒数

    返回字段:
        count                 - 实际返回的 App 数量
        avg_rating            - 样本 App 平均评分
        avg_reviews           - 样本 App 平均评论数
        top_reviews           - 第一名 App 的评论数（竞争强度核心指标）
        top_current_reviews   - 第一名 App 当前版本评论数
        avg_update_age_months - 前10个结果的平均更新距今月数（拿不到日期记为99）
        concentration         - 第1名评论数 / 前5名评论数之和（分母为0则记0）
    """
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

    # 当前版本评论数（第一名）
    top_current_reviews = apps[0].get("userRatingCountForCurrentVersion", 0)

    # 前10个结果的平均更新距今月数
    age_list = [
        _parse_update_age_months(a["currentVersionReleaseDate"])
        for a in apps
        if "currentVersionReleaseDate" in a
    ]
    # 拿不到日期的 App 补充 99
    age_list += [99] * (len(apps) - len(age_list))
    avg_update_age_months = int(sum(age_list) / len(age_list)) if age_list else 99

    # 市场集中度：第1名 / 前5名评论数之和
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
    """
    计算关键词机会分。

    公式：
        volume_proxy       = 20 - autocomplete_rank
        competition_strength = log10(top_reviews + 1)
        score              = volume_proxy / competition_strength

    分数越高 → 搜索量代理越大 且 竞争越弱 → 机会越好。

    参数:
        autocomplete_rank: Apple 补全位置（1 = 搜索量最高）
        competition:       get_competition() 返回的字典

    返回:
        机会分（float），保留 2 位小数。
        若 volume_proxy ≤ 0（排名 ≥ 20）则直接返回 0。
    """
    volume_proxy = 20 - autocomplete_rank
    if volume_proxy <= 0:
        return 0.0

    top_reviews = max(competition.get("top_reviews", 0), 0)
    competition_strength = math.log10(top_reviews + 1) if top_reviews > 0 else 1.0

    if competition_strength == 0:
        return round(float(volume_proxy) * 10, 2)

    return round(volume_proxy / competition_strength, 2)
