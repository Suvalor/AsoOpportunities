"""
全量关键词采集：从种子词出发，经 Autocomplete 与 iTunes 竞争数据得到结果列表。
不写 CSV、不计算蓝海分；rank_history 路径由环境变量 RANK_HISTORY_PATH 指定。
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from .autocomplete import get_autocomplete
from .competition import get_competition, opportunity_score
from .config import SEEDS

logger = logging.getLogger(__name__)

# trend_gap 查询的对比市场（主市场 us 之外）
TREND_COUNTRIES = ["gb", "au", "ca"]
TREND_SLEEP = 0.8
TREND_TOP_N = 50


def _rank_history_path() -> Path:
    return Path(os.getenv("RANK_HISTORY_PATH", "/data/rank_history.json"))


def compute_trend_gap(keyword: str, us_rank: int, sleep: float = TREND_SLEEP) -> float:
    """
    对同一关键词，查询 gb/au/ca 三国的 autocomplete 排名，
    返回 avg(其他国有效排名) - us_rank。
    找不到任何其他国家排名时返回 0。
    正数代表 US 领先（上升趋势信号）。
    """
    other_ranks: list[int] = []
    for country in TREND_COUNTRIES:
        try:
            completions = get_autocomplete(keyword, country=country, sleep=sleep)
            rank_in_country = next(
                (r for kw, r in completions if kw.lower() == keyword.lower()),
                None,
            )
            if rank_in_country is not None:
                other_ranks.append(rank_in_country)
        except Exception as exc:
            logger.warning("trend_gap 查询失败 [%s/%s]: %s", keyword, country, exc)

    if not other_ranks:
        return 0.0
    return round(sum(other_ranks) / len(other_ranks) - us_rank, 2)


def load_rank_history(rank_file: Path) -> dict:
    """读取 rank_history.json，返回 {date_str: {keyword: rank}} 字典。"""
    if not rank_file.exists():
        return {}
    try:
        with rank_file.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("读取 rank_history.json 失败: %s", exc)
        return {}


def compute_rank_changes(results: list[dict], history: dict) -> dict[str, int]:
    """
    对比上一次快照，计算每个关键词的排名变化。
    rank_change = prev_rank - current_rank（正数 = 排名上升）。
    历史少于2个日期快照时全部返回 0。
    """
    changes: dict[str, int] = {}
    if len(history) < 2:
        return changes

    sorted_dates = sorted(history.keys())
    prev_snapshot = history[sorted_dates[-1]]

    for r in results:
        kw = r["keyword"].lower().strip()
        prev_rank = prev_snapshot.get(kw)
        if prev_rank is not None:
            changes[kw] = int(prev_rank) - r["autocomplete_rank"]
    return changes


def save_rank_history(results: list[dict], history: dict, rank_file: Path) -> bool:
    """将本次所有 keyword:rank 以今天日期追加写入 rank_history.json。"""
    rank_file.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = {
        r["keyword"].lower().strip(): r["autocomplete_rank"] for r in results
    }
    history[today] = snapshot
    try:
        with rank_file.open("w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.warning("写入 rank_history.json 失败: %s", exc)
        return False


def run_full_scan(country: str = "us") -> list[dict]:
    """
    遍历全部种子词，去重后返回结果列表（不含蓝海分字段）。

    包含：seed_coverage、trend_gap、rank_change；写入 rank_history.json。
    """
    seeds = SEEDS
    best: dict[str, dict] = {}
    keyword_seeds: dict[str, set] = defaultdict(set)
    rank_file = _rank_history_path()

    for seed in tqdm(seeds, desc="种子词", unit="seed"):
        completions = get_autocomplete(seed, country=country)
        if not completions:
            logger.debug("种子词 [%s] 无补全结果，跳过", seed)
            continue

        for keyword, rank in tqdm(
            completions,
            desc=f"  {seed}",
            unit="kw",
            leave=False,
        ):
            comp = get_competition(keyword, country=country)
            score = opportunity_score(rank, comp)

            record = {
                "seed": seed,
                "keyword": keyword,
                "autocomplete_rank": rank,
                "top_app_reviews": comp["top_reviews"],
                "avg_reviews": comp["avg_reviews"],
                "avg_rating": comp["avg_rating"],
                "result_count": comp["count"],
                "opportunity_score": score,
                "top_current_reviews": comp["top_current_reviews"],
                "avg_update_age_months": comp["avg_update_age_months"],
                "concentration": comp["concentration"],
                "seed_coverage": 1,
                "trend_gap": 0.0,
                "rank_change": 0,
            }

            key = keyword.lower().strip()
            keyword_seeds[key].add(seed)
            if key not in best or score > best[key]["opportunity_score"]:
                best[key] = record

    if not best:
        logger.warning("没有找到任何关键词，请检查网络连接或种子词配置。")
        return []

    results = sorted(best.values(), key=lambda r: r["opportunity_score"], reverse=True)

    for r in results:
        key = r["keyword"].lower().strip()
        r["seed_coverage"] = len(keyword_seeds[key])

    logger.info("计算 Top %d 关键词的跨国趋势信号（trend_gap）...", TREND_TOP_N)
    for r in tqdm(results[:TREND_TOP_N], desc="trend_gap", unit="kw"):
        r["trend_gap"] = compute_trend_gap(r["keyword"], r["autocomplete_rank"])

    history = load_rank_history(rank_file)
    rank_changes = compute_rank_changes(results, history)
    for r in results:
        key = r["keyword"].lower().strip()
        r["rank_change"] = rank_changes.get(key, 0)

    save_rank_history(results, history, rank_file)

    return results
