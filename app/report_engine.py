"""
关键词洞察报告引擎：数据快照提取、触发判断、Prompt 构建、AI 生成。

每次生成报告时，将上一份报告的结论作为上下文传入 Claude，实现自迭代。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from .agent_client import call_agent
from .database import (
    get_keyword_snapshot_for_report,
    get_latest_report,
    get_recent_score_delta_sum,
    insert_report,
)

logger = logging.getLogger(__name__)


def get_current_keyword_snapshot() -> dict:
    """
    从最近14天数据中提取用于报告的关键词集合。
    返回结构化数据供 AI 和前端使用。
    """
    top_keywords, new_keyword_names = get_keyword_snapshot_for_report()

    gold_count = sum(1 for k in top_keywords if k.get("label") == "💎 金矿")
    ocean_count = sum(1 for k in top_keywords if k.get("label") == "🟢 蓝海")
    cross_platform_count = sum(1 for k in top_keywords if k.get("cross_platform"))
    trends_rising_count = sum(1 for k in top_keywords if k.get("trends_rising"))

    new_kw_set = set(new_keyword_names)
    new_keywords = [k for k in top_keywords if k["keyword"] in new_kw_set]

    now_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    return {
        "snapshot_at": now_str,
        "total_qualified": len(top_keywords),
        "gold_count": gold_count,
        "ocean_count": ocean_count,
        "new_keywords": new_keywords,
        "top_keywords": top_keywords,
        "cross_platform_count": cross_platform_count,
        "trends_rising_count": trends_rising_count,
    }


def should_generate_report() -> tuple[bool, str, dict]:
    """
    返回：(是否触发, 触发原因, 触发数据摘要)
    """
    latest = get_latest_report()

    if latest:
        last_time = latest["created_at"]
        if isinstance(last_time, str):
            last_time = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
        cooldown_hours = int(os.getenv("REPORT_COOLDOWN_HOURS", "20"))
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        hours_since = (now_utc - last_time).total_seconds() / 3600
        if hours_since < cooldown_hours:
            return False, "cooldown", {
                "hours_remaining": round(cooldown_hours - hours_since, 1),
            }

    snapshot = get_current_keyword_snapshot()

    prev_count = (latest.get("keyword_count") or 0) if latest else 0
    curr_count = snapshot["total_qualified"]
    count_delta = abs(curr_count - prev_count)

    new_gold = len([
        k for k in snapshot["new_keywords"]
        if k.get("label") == "💎 金矿"
    ])

    score_delta = get_recent_score_delta_sum(days=7)

    min_new_gold = int(os.getenv("REPORT_MIN_NEW_GOLD", "3"))
    min_delta = float(os.getenv("REPORT_MIN_SCORE_DELTA", "80"))
    min_kw_change = int(os.getenv("REPORT_MIN_KEYWORD_CHANGE", "10"))

    if new_gold >= min_new_gold:
        return True, "new_gold", {"new_gold": new_gold, "snapshot": snapshot}
    if score_delta >= min_delta:
        return True, "score_delta", {"score_delta": score_delta, "snapshot": snapshot}
    if count_delta >= min_kw_change:
        return True, "keyword_change", {"count_delta": count_delta, "snapshot": snapshot}

    return False, "no_threshold_met", {
        "new_gold": new_gold,
        "score_delta": score_delta,
        "count_delta": count_delta,
    }


def build_report_prompt(
    snapshot: dict, latest_report: dict | None
) -> tuple[str, int]:
    """
    构建发给 Claude 的 Prompt。
    将上次报告结论作为记忆上下文传入，实现自迭代。
    返回 (prompt_str, prompt_version)
    """
    prev_version = latest_report["prompt_version"] if latest_report else 0
    new_version = prev_version + 1

    memory_block = ""
    if latest_report:
        created = latest_report.get("created_at", "")
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M:%S")
        memory_block = f"""
## 上次报告记忆（{created} UTC，v{prev_version}）
以下是你上次的分析结论摘要，本次请在此基础上迭代，
指出哪些判断被数据验证了，哪些需要修正：

{str(latest_report.get('report_md', ''))[:2000]}
---
"""

    gold_list = [k for k in snapshot["top_keywords"] if k.get("label") == "💎 金矿"]
    ocean_list = [k for k in snapshot["top_keywords"] if k.get("label") == "🟢 蓝海"]
    new_list = snapshot.get("new_keywords", [])

    def fmt_kw(k: dict) -> str:
        flags = k.get("flags", "")
        return (
            f"  - {k['keyword']} | 分={k.get('peak_score', 0)} | "
            f"头部评论={k.get('top_reviews', 0)} | "
            f"跨平台={'是' if k.get('cross_platform') else '否'} | "
            f"Trends上升={'是' if k.get('trends_rising') else '否'} | "
            f"连续出现={k.get('days_seen', 1)}天 | {flags}"
        )

    gold_str = "\n".join(fmt_kw(k) for k in gold_list[:10]) or "（无）"
    ocean_str = "\n".join(fmt_kw(k) for k in ocean_list[:15]) or "（无）"
    new_str = "\n".join(fmt_kw(k) for k in new_list[:10]) or "（无）"

    prompt = f"""你是 App Store / Google Play 市场分析师，专注长尾需求挖掘。
本次为第 {new_version} 版分析报告。

{memory_block}

## 当前数据快照（{snapshot['snapshot_at']} UTC）
- 合格关键词总数：{snapshot['total_qualified']}
- 💎 金矿：{snapshot['gold_count']} 个
- 🟢 蓝海：{snapshot['ocean_count']} 个
- 双平台出现：{snapshot['cross_platform_count']} 个
- Google Trends 上升：{snapshot['trends_rising_count']} 个

### 💎 金矿词（最多10条）
{gold_str}

### 🟢 蓝海词（最多15条）
{ocean_str}

### 本周新出现高分词
{new_str}

## 输出要求
请用中文输出 Markdown 格式报告，包含以下章节，
不要输出章节以外的任何内容：

### 1. 本期核心信号（100字以内）
一句话概括本期数据最重要的变化。

### 2. 立项推荐（最多5个）
只推荐同时满足：peak_score >= 75、top_reviews < 5000、
跨平台或Trends上升至少一项为是 的关键词。
每个词单独一行：`关键词 | 推荐理由（30字以内）| 建议MVP方向（20字以内）`

### 3. 需求模式识别（最多3个）
从所有高分词中归纳用户需求的共同主题，每个主题举1-2个代表词。

### 4. 本期变化对比（与上次报告对比，首次报告跳过此章节）
- 新增机会：哪些词本期新出现且值得关注
- 已验证信号：上次推荐的词本期数据是否支撑
- 需要放弃：哪些词竞争已加剧

### 5. 风险提示（可选，有则写，无则省略）
当前数据中需要警惕的异常信号。
"""
    return prompt, new_version


def run_report_generation(triggered_by: str = "auto_threshold") -> dict:
    """
    完整报告生成流程：快照 -> Prompt -> 智能体调用 -> 入库。
    返回新报告的摘要字典。
    """
    snapshot = get_current_keyword_snapshot()
    latest_report = get_latest_report()
    prompt, prompt_version = build_report_prompt(snapshot, latest_report)

    report_md = call_agent("keyword_report", prompt, max_tokens=4000)

    new_gold = len([
        k for k in snapshot["new_keywords"]
        if k.get("label") == "💎 金矿"
    ])
    score_delta = get_recent_score_delta_sum(days=7)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    report_data = {
        "report_md": report_md,
        "triggered_by": triggered_by,
        "keyword_count": snapshot["total_qualified"],
        "new_gold_count": new_gold,
        "score_delta_sum": score_delta,
        "keywords_json": snapshot["top_keywords"],
        "prompt_version": prompt_version,
        "created_at": now_utc,
    }

    report_id = insert_report(report_data)

    logger.info(
        "报告生成完成 id=%d triggered_by=%s keyword_count=%d",
        report_id, triggered_by, snapshot["total_qualified"],
    )

    return {
        "report_id": report_id,
        "triggered_by": triggered_by,
        "keyword_count": snapshot["total_qualified"],
        "new_gold_count": new_gold,
        "created_at": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
    }
