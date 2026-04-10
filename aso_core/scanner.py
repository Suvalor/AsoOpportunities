"""
全量关键词采集：Autocomplete + iTunes 竞争数据，含 seed_coverage、trend_gap、rank_change。
不写 CSV、不计算蓝海分（由入口层调用 scorer）。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from tqdm import tqdm

from .autocomplete import get_autocomplete
from .competition import get_competition, opportunity_score
from .config_data import SEEDS
from .settings import get_settings

logger = logging.getLogger(__name__)

TREND_COUNTRIES = ["gb", "au", "ca"]
TREND_SLEEP = 0.8
TREND_TOP_N = 50


def compute_trend_gap(keyword: str, us_rank: int, sleep: float = TREND_SLEEP) -> float:
    """
    查询 gb/au/ca 三国 autocomplete 排名，返回 avg(其他国有效排名) - us_rank。
    找不到任何其他国家排名时返回 0。
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
    """读取 rank_history.json，返回 {date_str: {keyword: rank}}。"""
    if not rank_file.exists():
        return {}
    try:
        with rank_file.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("读取 rank_history.json 失败: %s", exc)
        return {}


def compute_rank_changes(results: list[dict], history: dict) -> dict[str, int]:
    """rank_change = prev_rank - current_rank（正数表示排名上升）；历史少于 2 个快照时为空。"""
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
    """以今天日期为 key 合并写入 rank_history.json。"""
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


def _ingest_record(
    best: dict[str, dict],
    keyword_seeds: dict[str, set],
    seed: str,
    keyword: str,
    rank: int,
    country: str,
) -> None:
    """单条关键词写入去重字典（保留更高 opportunity_score）。"""
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


def run_full_scan(
    country: str | None = None,
    seeds: Sequence[str] | None = None,
    rank_history_path: Path | str | None = None,
    mode: str = "full",
) -> list[dict]:
    """
    遍历种子词或追踪词列表，去重后返回结果列表（不含蓝海分字段）。

    :param country: 主市场；None 时使用 settings.default_country
    :param seeds: full 模式下为 None 时用 SEEDS；tracking 模式下为待刷新的关键词列表（必填）
    :param rank_history_path: 排名历史文件路径；None 时使用 settings.rank_history_path
    :param mode: ``full`` 为完整种子矩阵；``tracking`` 仅刷新传入的关键词（逐词查补全位次与竞争）
    """
    s = get_settings()
    if country is None:
        country = s.default_country
    mode = (mode or "full").strip().lower()
    if mode not in ("full", "tracking"):
        mode = "full"

    if mode == "tracking":
        if not seeds:
            logger.warning("tracking 模式未提供关键词列表，跳过扫描。")
            return []
        seed_list = list(seeds)
    elif seeds is None:
        seed_list = list(SEEDS)
    else:
        seed_list = list(seeds)

    if rank_history_path is None:
        rank_file = s.rank_history_path
    else:
        rank_file = Path(rank_history_path)

    best: dict[str, dict] = {}
    keyword_seeds: dict[str, set] = defaultdict(set)

    if mode == "tracking":
        for keyword in tqdm(seed_list, desc="追踪关键词", unit="kw"):
            completions = get_autocomplete(keyword, country=country)
            rank = next(
                (r for kw, r in completions if kw.lower() == keyword.lower()),
                None,
            )
            if rank is None:
                rank = 21
            _ingest_record(best, keyword_seeds, keyword, keyword, rank, country)
    else:
        for seed in tqdm(seed_list, desc="种子词", unit="seed"):
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
                _ingest_record(best, keyword_seeds, seed, keyword, rank, country)

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
