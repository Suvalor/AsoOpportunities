"""
Google Trends 信号采集：获取上升相关查询并判断关键词是否命中。
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

_request_counter = 0


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


def _compute_slope(timeline: list[dict]) -> float:
    """对 timeline 数据做线性回归，返回斜率。"""
    n = len(timeline)
    if n < 2:
        return 0.0

    xs = list(range(n))
    ys = [float(item["value"]) for item in timeline]

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)

    denominator = n * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return 0.0

    return (n * sum_xy - sum_x * sum_y) / denominator


def _compute_segment_slopes(timeline: list[dict], segment_days: int = 30) -> list[float]:
    """按 segment_days 天分段计算斜率。"""
    if not timeline:
        return []

    segments: list[float] = []
    i = 0
    while i < len(timeline):
        end = min(i + segment_days, len(timeline))
        segment = timeline[i:end]
        segments.append(_compute_slope(segment))
        i = end

    return segments


def get_trends_interest_over_time(
    keyword: str,
    timeframe: str | None = None,
    geo: str = "US",
    sleep: float | None = None,
) -> dict:
    """
    获取关键词的 Google Trends 搜索兴趣时序数据。

    返回：
    {
        "timeline": list[dict],  -- [{date: str, value: float}, ...]
        "avg_interest": float,   -- 时间窗口内平均兴趣值 (0-100)
        "volume_tier": int,      -- 搜索量级分层 (0-5)
        "slope": float,          -- 趋势斜率（线性回归）
        "slope_segments": list,  -- 分段斜率（每30天一段）
    }

    volume_tier 分层：
      5 = avg_interest >= 75
      4 = avg_interest >= 50
      3 = avg_interest >= 25
      2 = avg_interest >= 10
      1 = avg_interest > 0
      0 = avg_interest == 0 or 数据缺失
    """
    global _request_counter

    tf = timeframe or os.getenv("TRENDS_IOT_TIMEFRAME", "today 3-m")
    sleep_sec = sleep if sleep is not None else max(float(os.getenv("TRENDS_IOT_SLEEP", "1.5")), 1.0)

    timeline: list[dict] = []

    try:
        from pytrends.request import TrendReq

        pt = TrendReq(
            hl="en-US",
            tz=0,
            timeout=(10, 25),
            retries=2,
            backoff_factor=0.5,
        )

        _request_counter += 1
        if _request_counter % 5 == 0:
            time.sleep(5)

        pt.build_payload([keyword], timeframe=tf, geo=geo)

        iot_df = None
        retries = 3
        backoff_times = [5, 10, 20]
        for attempt in range(retries):
            try:
                iot_df = pt.interest_over_time()
                break
            except Exception as exc:
                if "429" in str(exc) and attempt < retries - 1:
                    wait = backoff_times[attempt]
                    logger.warning(
                        "[Trends IoT] %s 收到 429，退避 %ds 后重试 (%d/%d)",
                        keyword, wait, attempt + 1, retries,
                    )
                    time.sleep(wait)
                else:
                    raise

        time.sleep(sleep_sec)

        if iot_df is not None and not iot_df.empty:
            col = keyword if keyword in iot_df.columns else iot_df.columns[0]
            if "isPartial" in iot_df.columns:
                iot_df = iot_df[iot_df["isPartial"] == False]

            for idx, row in iot_df.iterrows():
                timeline.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "value": float(row[col]),
                })

    except Exception as exc:
        logger.warning("[Trends IoT] %s 查询失败: %s", keyword, exc)

    if not timeline:
        return {
            "timeline": [],
            "avg_interest": 0.0,
            "volume_tier": 0,
            "slope": 0.0,
            "slope_segments": [],
        }

    values = [item["value"] for item in timeline]
    avg_interest = sum(values) / len(values)

    if avg_interest >= 75:
        volume_tier = 5
    elif avg_interest >= 50:
        volume_tier = 4
    elif avg_interest >= 25:
        volume_tier = 3
    elif avg_interest >= 10:
        volume_tier = 2
    elif avg_interest > 0:
        volume_tier = 1
    else:
        volume_tier = 0

    slope = _compute_slope(timeline)
    slope_segments = _compute_segment_slopes(timeline, segment_days=30)

    return {
        "timeline": timeline,
        "avg_interest": round(avg_interest, 2),
        "volume_tier": volume_tier,
        "slope": round(slope, 4),
        "slope_segments": [round(s, 4) for s in slope_segments],
    }
