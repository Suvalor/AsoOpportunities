"""
封装 Apple App Store Autocomplete API。

URL: https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints
"""

from __future__ import annotations

import plistlib
import time
import logging

import requests

from .settings import get_settings

logger = logging.getLogger(__name__)

AUTOCOMPLETE_URL = (
    "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
)

_HEADERS = {
    "User-Agent": "iTunes/12.12.9 (Macintosh; OS X 13.0) AppleWebKit/7617.5.30.20.3",
    "X-Apple-Store-Front": "143441-1,32",
}


def get_autocomplete(
    term: str,
    country: str | None = None,
    limit: int | None = None,
    sleep: float | None = None,
) -> list[tuple[str, int]]:
    """
    查询 Apple Autocomplete API，返回补全词及其排名。

    未传入的 country / limit / sleep 由 get_settings() 提供默认值。
    """
    s = get_settings()
    if country is None:
        country = s.default_country
    if limit is None:
        limit = s.autocomplete_limit
    if sleep is None:
        sleep = s.rate_limit_sleep

    params = {
        "clientApplication": "Software",
        "term": term,
        "limit": limit,
        "country": country,
    }

    try:
        response = requests.get(
            AUTOCOMPLETE_URL, params=params, headers=_HEADERS, timeout=10
        )
        response.raise_for_status()
        data = plistlib.loads(response.content)
        hints = data.get("hints", [])
        # 只在请求成功时 sleep，避免失败时不必要的延迟
        time.sleep(sleep)
    except requests.RequestException as exc:
        logger.warning("Autocomplete 请求失败 [%s]: %s", term, exc)
        return []
    except Exception as exc:
        logger.warning("Autocomplete 解析失败 [%s]: %s", term, exc)
        return []

    return [(h["term"], idx + 1) for idx, h in enumerate(hints) if "term" in h]
