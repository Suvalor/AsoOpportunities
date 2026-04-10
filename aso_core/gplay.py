"""
Google Play Store 数据采集：Autocomplete 补全 + 竞争数据。

备用 Autocomplete 端点（主端点失效时可替换）：
  https://play.google.com/store/xhr/search?protocol=2&ipf=1&xhr=1
"""

from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

GPLAY_SUGGEST_URL = "https://market.android.com/suggest/SuggResponse"


def _parse_installs(raw: str) -> int:
    """将 "1,000,000+" 解析为整数 1000000；无法解析返回 0。"""
    try:
        return int(str(raw).replace(",", "").replace("+", "").strip())
    except (ValueError, TypeError):
        return 0


def get_gplay_autocomplete(
    term: str,
    country: str = "us",
    lang: str | None = None,
) -> list[tuple[str, int]]:
    """
    调用 Google Play 补全接口，返回 [(keyword, rank), ...]。
    格式与 Apple Autocomplete 一致，rank 从 1 开始。
    """
    if lang is None:
        lang = os.getenv("GPLAY_DEFAULT_LANG", "en")

    params = {
        "json": "1",
        "c": "3",
        "query": term,
        "hl": lang,
        "gl": country,
    }
    try:
        resp = requests.get(GPLAY_SUGGEST_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results: list[tuple[str, int]] = []
        for idx, item in enumerate(data):
            kw = item.get("s", "").strip()
            if kw:
                results.append((kw, idx + 1))
        return results
    except Exception as exc:
        logger.warning("[GPlay] autocomplete 请求失败 [%s]: %s", term, exc)
        return []


def get_gplay_competition(keyword: str, country: str = "us") -> dict:
    """
    使用 google-play-scraper 搜索关键词，返回竞争数据。
    """
    empty = {
        "count": 0,
        "top_reviews": 0,
        "avg_reviews": 0,
        "top_installs": "0",
        "top_installs_num": 0,
        "avg_rating": 0.0,
    }
    try:
        from google_play_scraper import search

        results = search(keyword, lang="en", country=country, n_hits=10)
        if not results:
            return empty

        ratings_list = [r.get("ratings", 0) or 0 for r in results]
        score_list = [r.get("score", 0) or 0 for r in results]
        top_installs_raw = str(results[0].get("installs", "0") or "0")

        return {
            "count": len(results),
            "top_reviews": ratings_list[0],
            "avg_reviews": int(sum(ratings_list) / len(ratings_list)) if ratings_list else 0,
            "top_installs": top_installs_raw,
            "top_installs_num": _parse_installs(top_installs_raw),
            "avg_rating": round(sum(score_list) / len(score_list), 2) if score_list else 0.0,
        }
    except Exception as exc:
        logger.warning("[GPlay] competition 查询失败 [%s]: %s", keyword, exc)
        return empty
