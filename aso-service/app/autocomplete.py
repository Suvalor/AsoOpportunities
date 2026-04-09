"""
封装 Apple App Store Autocomplete API。

接口说明：
  Apple 官方补全接口，多年来一直稳定存在。
  返回的 hints 列表按真实搜索频率降序排列——位置越靠前，搜索量越大。
  这个排序顺序本身就是免费的搜索量代理指标。

注意：
  - 响应格式为 XML plist（Content-Type: text/xml），不是 JSON，需用 plistlib 解析。
  - 必须携带 iTunes 客户端 User-Agent，否则 Apple 返回空的 hints 列表。

URL: https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints
"""

import plistlib
import time
import logging

import requests

from .config import COUNTRY, AUTOCOMPLETE_LIMIT, RATE_LIMIT_SLEEP

logger = logging.getLogger(__name__)

AUTOCOMPLETE_URL = (
    "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
)

# Apple 校验客户端标识：缺少此头部时返回空 hints
_HEADERS = {
    "User-Agent": "iTunes/12.12.9 (Macintosh; OS X 13.0) AppleWebKit/7617.5.30.20.3",
    "X-Apple-Store-Front": "143441-1,32",
}


def get_autocomplete(
    term: str,
    country: str = COUNTRY,
    limit: int = AUTOCOMPLETE_LIMIT,
    sleep: float = RATE_LIMIT_SLEEP,
) -> list[tuple[str, int]]:
    """
    查询 Apple Autocomplete API，返回补全词及其排名。

    排名从 1 开始，1 = 搜索量最高，数字越大搜索量越低。

    参数:
        term:    种子关键词
        country: 市场区域代码，默认来自 config.COUNTRY
        limit:   最多返回多少个补全词
        sleep:   请求后等待秒数（避免速率限制）

    返回:
        [(keyword, rank), ...]，按搜索频率升序排列（rank=1 最高）
        若请求失败则返回空列表。
    """
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
        # 响应为 XML plist，使用 plistlib 解析（不能用 response.json()）
        data = plistlib.loads(response.content)
        hints = data.get("hints", [])
    except requests.RequestException as exc:
        logger.warning("Autocomplete 请求失败 [%s]: %s", term, exc)
        return []
    except Exception as exc:
        logger.warning("Autocomplete 解析失败 [%s]: %s", term, exc)
        return []
    finally:
        time.sleep(sleep)

    return [(h["term"], idx + 1) for idx, h in enumerate(hints) if "term" in h]
