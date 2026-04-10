"""
Reddit 需求验证：搜索相关 subreddit 帖子，量化用户讨论热度。

默认关闭（ENABLE_REDDIT=false），需用户在 https://www.reddit.com/prefs/apps
申请 API Key 后手动开启。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_SUBREDDITS = [
    "androidapps",
    "iosapps",
    "apps",
    "productivity",
    "selfhosted",
    "androidquestions",
]


def get_reddit_demand_signal(
    keyword: str,
    subreddits: list[str] | None = None,
) -> dict:
    """
    搜索多个 subreddit 中与 keyword 相关的帖子，返回讨论热度指标。
    ENABLE_REDDIT=false 时直接返回零值。
    """
    empty = {"post_count": 0, "avg_score": 0, "top_title": ""}

    if os.getenv("ENABLE_REDDIT", "false").lower() != "true":
        return empty

    try:
        import praw

        reddit = praw.Reddit(
            client_id=os.getenv("REDDIT_CLIENT_ID"),
            client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
            user_agent=os.getenv("REDDIT_USER_AGENT", "aso-keyword-engine/1.0"),
        )
        subs = subreddits or DEFAULT_SUBREDDITS
        sub_str = "+".join(subs)

        results = list(
            reddit.subreddit(sub_str).search(
                keyword, sort="relevance", time_filter="year", limit=10
            )
        )
        if not results:
            return empty

        scores = [p.score for p in results]
        return {
            "post_count": len(results),
            "avg_score": round(sum(scores) / len(scores), 1),
            "top_title": results[0].title[:200],
        }
    except Exception as exc:
        logger.warning("[Reddit] %s 查询失败: %s", keyword, exc)
        return empty
