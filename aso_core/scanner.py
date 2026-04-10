"""
全量关键词采集：Autocomplete + iTunes 竞争数据，含 seed_coverage、trend_gap、rank_change。
支持多国家：按 ASO_SCAN_COUNTRIES 顺序逐国扫描，每条结果含 country 字段。
"""

from __future__ import annotations

import json
import logging
import os
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

# 模块级国家配置（可被环境变量覆盖）
PRIMARY_COUNTRY = os.getenv("ASO_PRIMARY_COUNTRY", "us").strip().lower() or "us"
SCAN_COUNTRIES = [
    c.strip().lower()
    for c in os.getenv("ASO_SCAN_COUNTRIES", "us").split(",")
    if c.strip()
]
TREND_COUNTRIES = [
    c.strip().lower()
    for c in os.getenv("ASO_TREND_COUNTRIES", "gb,au,ca").split(",")
    if c.strip()
]

TREND_SLEEP = 0.8
TREND_TOP_N = 50


def _history_key(country: str, keyword: str) -> str:
    return f"{country.lower().strip()}|{keyword.lower().strip()}"


def _normalize_snapshot_keys(snap: dict) -> dict[str, int]:
    """兼容旧版 rank_history（仅 keyword 为键）：视为 PRIMARY_COUNTRY 下数据。"""
    out: dict[str, int] = {}
    for k, v in snap.items():
        ks = str(k).lower().strip()
        if "|" in ks:
            out[ks] = int(v)
        else:
            out[_history_key(PRIMARY_COUNTRY, ks)] = int(v)
    return out


def _lookup_autocomplete_rank(keyword: str, country: str, sleep: float) -> int | None:
    try:
        completions = get_autocomplete(keyword, country=country, sleep=sleep)
        r = next(
            (rk for kw, rk in completions if kw.lower() == keyword.lower()),
            None,
        )
        return int(r) if r is not None else None
    except Exception as exc:
        logger.warning("trend_gap 查排名失败 [%s/%s]: %s", keyword, country, exc)
        return None


def compute_trend_gap(
    keyword: str,
    rank_in_scan_country: int,
    scan_country: str,
    sleep: float = TREND_SLEEP,
) -> float:
    """
    以 PRIMARY_COUNTRY 的排名为基准，与 TREND_COUNTRIES 中各国排名对比。
    返回 avg(对比国有效排名) - 主市场排名；无主市场排名或无对比国数据时返回 0。

    配置上与文档一致：all_trend_countries = [PRIMARY_COUNTRY] + TREND_COUNTRIES；
    差分值仍按「TREND_COUNTRIES 各国均位 − 主市场位」计算。
    """
    sc = scan_country.strip().lower()
    if sc == PRIMARY_COUNTRY:
        primary_rank = rank_in_scan_country
    else:
        pr = _lookup_autocomplete_rank(keyword, PRIMARY_COUNTRY, sleep=sleep)
        if pr is None:
            return 0.0
        primary_rank = pr

    other_ranks: list[int] = []
    for country in TREND_COUNTRIES:
        if country == PRIMARY_COUNTRY:
            continue
        r = _lookup_autocomplete_rank(keyword, country, sleep=sleep)
        if r is not None:
            other_ranks.append(r)

    if not other_ranks:
        return 0.0
    return round(sum(other_ranks) / len(other_ranks) - primary_rank, 2)


def load_rank_history(rank_file: Path) -> dict:
    """读取 rank_history.json，返回 {date_str: {composite_key: rank}}。"""
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
    rank_change = prev_rank - current_rank（正数表示排名上升）；历史少于 2 个快照时为空。
    键为 composite_key（country|keyword）。
    """
    changes: dict[str, int] = {}
    if len(history) < 2:
        return changes

    sorted_dates = sorted(history.keys())
    prev_raw = history[sorted_dates[-1]]
    prev_snapshot = _normalize_snapshot_keys(prev_raw if isinstance(prev_raw, dict) else {})

    for r in results:
        kw = r["keyword"].lower().strip()
        cc = str(r.get("country") or PRIMARY_COUNTRY).lower().strip()
        key = _history_key(cc, kw)
        prev_rank = prev_snapshot.get(key)
        if prev_rank is not None:
            changes[key] = int(prev_rank) - r["autocomplete_rank"]
    return changes


def save_rank_history(results: list[dict], history: dict, rank_file: Path) -> bool:
    """以今天日期为 key 合并写入 rank_history.json（键为 country|keyword）。"""
    rank_file.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = {
        _history_key(str(r.get("country") or PRIMARY_COUNTRY), r["keyword"]): r[
            "autocomplete_rank"
        ]
        for r in results
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
    """单条关键词写入去重字典（同一国家内保留更高 opportunity_score）。"""
    comp = get_competition(keyword, country=country)
    score = opportunity_score(rank, comp)
    record = {
        "country": country,
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
    key = _history_key(country, keyword)
    keyword_seeds[key].add(seed)
    if key not in best or score > best[key]["opportunity_score"]:
        best[key] = record


def _scan_single_country(
    country: str,
    seeds: list[str],
    mode: str,
) -> list[dict]:
    """单国：种子展开 → 去重 → seed_coverage → trend_gap（Top N）。"""
    best: dict[str, dict] = {}
    keyword_seeds: dict[str, set] = defaultdict(set)

    desc = "追踪种子" if mode == "tracking" else "种子词"
    label = f"{desc}[{country}]"
    for seed in tqdm(seeds, desc=label, unit="seed"):
        completions = get_autocomplete(seed, country=country)
        if not completions:
            logger.debug("种子词 [%s] 在 %s 无补全结果，跳过", seed, country)
            continue

        for keyword, rank in tqdm(
            completions,
            desc=f"  {seed}",
            unit="kw",
            leave=False,
        ):
            _ingest_record(best, keyword_seeds, seed, keyword, rank, country)

    if not best:
        logger.warning("国家 %s 未得到任何关键词。", country)
        return []

    results = sorted(best.values(), key=lambda r: r["opportunity_score"], reverse=True)

    for r in results:
        key = _history_key(country, r["keyword"])
        r["seed_coverage"] = len(keyword_seeds[key])

    logger.info(
        "国家 %s：计算 Top %d 关键词的跨国趋势信号（trend_gap，主市场=%s，对比国=%s）...",
        country,
        TREND_TOP_N,
        PRIMARY_COUNTRY,
        ",".join(TREND_COUNTRIES) or "(无)",
    )
    for r in tqdm(
        results[:TREND_TOP_N],
        desc=f"trend_gap[{country}]",
        unit="kw",
    ):
        r["trend_gap"] = compute_trend_gap(
            r["keyword"],
            r["autocomplete_rank"],
            country,
        )

    return results


def run_full_scan(
    countries: list[str] | None = None,
    seeds: Sequence[str] | None = None,
    rank_history_path: Path | str | None = None,
    mode: str = "full",
) -> list[dict]:
    """
    按国家列表依次执行种子矩阵扫描，合并结果；每条记录含 country。

    :param countries: 为 None 时使用环境变量 ASO_SCAN_COUNTRIES 解析后的 SCAN_COUNTRIES
    :param seeds: 为 None 且 mode=full 时用内置 SEEDS（CLI）；服务侧传入 DB 种子列表
    :param rank_history_path: 排名历史文件路径；None 时用 settings.rank_history_path
    :param mode: full 或 tracking（均按种子展开 autocomplete）
    """
    s = get_settings()
    mode = (mode or "full").strip().lower()
    if mode not in ("full", "tracking"):
        mode = "full"

    if countries is None:
        countries = list(SCAN_COUNTRIES)
    countries = [c.strip().lower() for c in countries if c and str(c).strip()]
    if not countries:
        logger.warning("国家列表为空，跳过扫描。")
        return []

    if not seeds:
        if mode == "tracking":
            logger.warning("tracking 模式未提供种子列表，跳过扫描。")
            return []
        seed_list = list(SEEDS)
    else:
        seed_list = list(seeds)

    if rank_history_path is None:
        rank_file = s.rank_history_path
    else:
        rank_file = Path(rank_history_path)

    all_results: list[dict] = []
    for country in countries:
        part = _scan_single_country(country, seed_list, mode)
        all_results.extend(part)

    if not all_results:
        logger.warning("没有找到任何关键词，请检查网络连接或种子词配置。")
        return []

    history = load_rank_history(rank_file)
    rank_changes = compute_rank_changes(all_results, history)
    for r in all_results:
        key = _history_key(str(r.get("country") or PRIMARY_COUNTRY), r["keyword"])
        r["rank_change"] = rank_changes.get(key, 0)

    save_rank_history(all_results, history, rank_file)

    return all_results
