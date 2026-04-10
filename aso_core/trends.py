"""
Google Trends 信号采集：获取上升相关查询并判断关键词是否命中。
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


def get_trends_rising_queries(
    keyword: str,
    timeframe: str | None = None,
    geo: str = "US",
) -> list[str]:
    """
    查询 Google Trends 中 keyword 的上升相关查询。
    返回 rising query 字符串列表；失败时返回空列表。
    """
    tf = timeframe or os.getenv("TRENDS_TIMEFRAME", "today 3-m")
    sleep_sec = max(float(os.getenv("TRENDS_SLEEP", "1.0")), 0.8)

    try:
        from pytrends.request import TrendReq

        pt = TrendReq(
            hl="en-US",
            tz=0,
            timeout=(10, 25),
            retries=2,
            backoff_factor=0.5,
        )
        pt.build_payload([keyword], timeframe=tf, geo=geo)
        time.sleep(sleep_sec)

        related = pt.related_queries()
        rising_df = related.get(keyword, {}).get("rising")
        if rising_df is not None and not rising_df.empty:
            return rising_df["query"].tolist()
    except Exception as exc:
        logger.warning("[Trends] %s 查询失败: %s", keyword, exc)

    return []


def keyword_in_rising(keyword: str, rising_list: list[str]) -> bool:
    """
    判断 keyword 是否出现在 rising_list 中。
    大小写不敏感，部分匹配：任一 rising 词包含 keyword 或 keyword 包含 rising 词。
    """
    kw_lower = keyword.lower()
    for q in rising_list:
        q_lower = q.lower()
        if kw_lower in q_lower or q_lower in kw_lower:
            return True
    return False
