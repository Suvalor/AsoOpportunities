"""
ASO 蓝海关键词分析系统 — 主流程

用法:
    python main.py               # 完整跑所有 240 个种子词（约 40 分钟）
    python main.py --seeds 20    # 只跑前 N 个种子（测试用）
    python main.py --country gb  # 指定主市场区域
    python main.py --out results.csv  # 自定义输出文件名
"""

import argparse
import csv
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from config import SEEDS
from autocomplete import get_autocomplete
from competition import get_competition, opportunity_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

RANK_HISTORY_FILE = Path("rank_history.json")

# trend_gap 查询的对比市场（主市场 us 之外）
TREND_COUNTRIES = ["gb", "au", "ca"]
TREND_SLEEP = 0.8
# 只对 opportunity_score 前 N 名触发 trend_gap 查询
TREND_TOP_N = 50

OUTPUT_FIELDS = [
    "seed",
    "keyword",
    "autocomplete_rank",
    "top_app_reviews",
    "avg_reviews",
    "avg_rating",
    "result_count",
    "opportunity_score",
    # 新增字段
    "seed_coverage",
    "top_current_reviews",
    "avg_update_age_months",
    "concentration",
    "trend_gap",
    "rank_change",
    "blue_ocean_score",
    "blue_ocean_flags",
    "blue_ocean_label",
]


# ---------------------------------------------------------------------------
# 蓝海评分
# ---------------------------------------------------------------------------

def blue_ocean_score(record: dict) -> tuple[float, str]:
    """
    输入一条 result 字典，返回 (score, flags_str)。

    评分规则（满分 110 分）：
      搜索量真实性（最高30分）
        seed_coverage >= 3 → +30，"多路径触发"
        seed_coverage == 2 → +15
      竞争强度（最高50分）
        top_reviews < 1000   → +40，"头部极弱"
        top_reviews 1000–4999 → +25，"竞争低"
        top_reviews 5000–19999 → +10
        concentration < 0.3  → +10，"市场分散"
        avg_update_age_months > 12 → +10，"竞品躺平"
      趋势（最高30分）
        trend_gap > 3  → +20，"US领先趋势"
        trend_gap 1–3  → +10
        rank_change > 2 → +10，"排名上升"
    """
    score = 0.0
    flags: list[str] = []

    # 搜索量真实性
    coverage = record.get("seed_coverage", 1)
    if coverage >= 3:
        score += 30
        flags.append("多路径触发")
    elif coverage == 2:
        score += 15

    # 竞争强度
    top_reviews = record.get("top_app_reviews", 0)
    if top_reviews < 1000:
        score += 40
        flags.append("头部极弱")
    elif top_reviews < 5000:
        score += 25
        flags.append("竞争低")
    elif top_reviews < 20000:
        score += 10

    concentration = record.get("concentration", 0.0)
    if concentration < 0.3:
        score += 10
        flags.append("市场分散")

    avg_age = record.get("avg_update_age_months", 0)
    if avg_age > 12:
        score += 10
        flags.append("竞品躺平")

    # 趋势
    trend_gap = record.get("trend_gap", 0)
    if trend_gap > 3:
        score += 20
        flags.append("US领先趋势")
    elif trend_gap >= 1:
        score += 10

    rank_change = record.get("rank_change", 0)
    if rank_change > 2:
        score += 10
        flags.append("排名上升")

    return score, " | ".join(flags)


def blue_ocean_label(score: float) -> str:
    if score >= 80:
        return "💎 金矿"
    if score >= 60:
        return "🟢 蓝海"
    if score >= 40:
        return "🟡 观察"
    return "🔴 跳过"


# ---------------------------------------------------------------------------
# trend_gap：跨国家趋势信号
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# rank_change：历史排名变化
# ---------------------------------------------------------------------------

def load_rank_history() -> dict:
    """读取 rank_history.json，返回 {date_str: {keyword: rank}} 字典。"""
    if not RANK_HISTORY_FILE.exists():
        return {}
    try:
        with RANK_HISTORY_FILE.open(encoding="utf-8") as f:
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
    prev_snapshot = history[sorted_dates[-1]]  # 最近一次历史

    for r in results:
        kw = r["keyword"].lower().strip()
        prev_rank = prev_snapshot.get(kw)
        if prev_rank is not None:
            changes[kw] = int(prev_rank) - r["autocomplete_rank"]
    return changes


def save_rank_history(results: list[dict], history: dict) -> bool:
    """将本次所有 keyword:rank 以今天日期追加写入 rank_history.json。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = {
        r["keyword"].lower().strip(): r["autocomplete_rank"] for r in results
    }
    history[today] = snapshot
    try:
        with RANK_HISTORY_FILE.open("w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.warning("写入 rank_history.json 失败: %s", exc)
        return False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(seeds: list[str], country: str, out_path: Path) -> None:
    """
    主流程：
      1. 遍历种子词，拿 Autocomplete 补全词
      2. 对每个补全词查 iTunes API 拿竞争数据 + 计算 opportunity_score
      3. 去重（同关键词保留最高 opportunity_score 的记录）
      4. 回填 seed_coverage（全部种子跑完后统一计算）
      5. 对 Top 50 计算 trend_gap
      6. 读取 rank_history.json，计算 rank_change
      7. 计算 blue_ocean_score，写 CSV（按 blue_ocean_score 降序）
      8. 追加写入 rank_history.json
      9. 打印汇总
    """
    # key: 关键词小写 → 已记录的最高分记录
    best: dict[str, dict] = {}
    # 统计同一关键词被多少个不同种子触发（全量收集后回填）
    keyword_seeds: dict[str, set] = defaultdict(set)

    # --- Step 1 & 2：采集 ---
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
                # 新增竞争字段
                "top_current_reviews": comp["top_current_reviews"],
                "avg_update_age_months": comp["avg_update_age_months"],
                "concentration": comp["concentration"],
                # 待回填字段，先置默认值
                "seed_coverage": 1,
                "trend_gap": 0.0,
                "rank_change": 0,
                "blue_ocean_score": 0.0,
                "blue_ocean_flags": "",
                "blue_ocean_label": "",
            }

            key = keyword.lower().strip()
            keyword_seeds[key].add(seed)
            if key not in best or score > best[key]["opportunity_score"]:
                best[key] = record

    if not best:
        logger.warning("没有找到任何关键词，请检查网络连接或种子词配置。")
        return

    # --- Step 3：去重后转列表，按 opportunity_score 排序 ---
    results = sorted(best.values(), key=lambda r: r["opportunity_score"], reverse=True)

    # --- Step 4：回填 seed_coverage ---
    for r in results:
        key = r["keyword"].lower().strip()
        r["seed_coverage"] = len(keyword_seeds[key])

    # --- Step 5：Top 50 计算 trend_gap ---
    logger.info("计算 Top %d 关键词的跨国趋势信号（trend_gap）...", TREND_TOP_N)
    for r in tqdm(results[:TREND_TOP_N], desc="trend_gap", unit="kw"):
        r["trend_gap"] = compute_trend_gap(r["keyword"], r["autocomplete_rank"])

    # --- Step 6：读取历史，计算 rank_change ---
    history = load_rank_history()
    rank_changes = compute_rank_changes(results, history)
    for r in results:
        key = r["keyword"].lower().strip()
        r["rank_change"] = rank_changes.get(key, 0)

    # --- Step 7：计算 blue_ocean_score，重新排序并写 CSV ---
    for r in results:
        bos, flags = blue_ocean_score(r)
        r["blue_ocean_score"] = bos
        r["blue_ocean_flags"] = flags
        r["blue_ocean_label"] = blue_ocean_label(bos)

    results.sort(key=lambda r: r["blue_ocean_score"], reverse=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    logger.info("结果已写入 %s（共 %d 条）", out_path, len(results))

    # --- Step 8：保存本次排名历史 ---
    history_saved = save_rank_history(results, history)

    # --- Step 9：控制台汇总 ---
    _print_summary(results, history_saved)


def _print_summary(results: list[dict], history_saved: bool) -> None:
    """打印蓝海分析汇总报告。"""
    gold = [r for r in results if r["blue_ocean_score"] >= 80]
    blue = [r for r in results if 60 <= r["blue_ocean_score"] < 80]
    watch = [r for r in results if 40 <= r["blue_ocean_score"] < 60]
    skip = [r for r in results if r["blue_ocean_score"] < 40]

    print("\n" + "=" * 60)
    print(f"  ASO 蓝海分析完成 — 共 {len(results)} 个关键词")
    print("=" * 60)

    print(f"\n💎 金矿（≥80分）：{len(gold)} 个")
    for r in gold:
        print(f"   [{r['blue_ocean_score']:>5.1f}] {r['keyword']}  |  {r['blue_ocean_flags']}")

    print(f"\n🟢 蓝海（60-79分）：{len(blue)} 个")
    for r in blue:
        print(f"   [{r['blue_ocean_score']:>5.1f}] {r['keyword']}  |  {r['blue_ocean_flags']}")

    print(f"\n🟡 观察（40-59分）：{len(watch)} 个")
    print(f"🔴 跳过（<40分）：{len(skip)} 个")

    status = "✅ 成功" if history_saved else "❌ 失败"
    print(f"\nrank_history.json 写入：{status}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ASO 蓝海关键词分析系统")
    parser.add_argument(
        "--seeds",
        type=int,
        default=None,
        metavar="N",
        help="只跑前 N 个种子词（不指定则跑全部）",
    )
    parser.add_argument(
        "--country",
        type=str,
        default=None,
        metavar="CODE",
        help="主市场区域代码，默认使用 config.py 中的 COUNTRY",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="aso_opportunities.csv",
        metavar="FILE",
        help="输出 CSV 文件路径（默认：aso_opportunities.csv）",
    )
    args = parser.parse_args()

    seeds = SEEDS[: args.seeds] if args.seeds else SEEDS
    logger.info("共 %d 个种子词，预计约 %.0f 分钟", len(seeds), len(seeds) * 20 * 0.5 / 60)

    from config import COUNTRY as DEFAULT_COUNTRY
    country = args.country or DEFAULT_COUNTRY

    out_path = Path(args.out)

    try:
        run(seeds=seeds, country=country, out_path=out_path)
    except KeyboardInterrupt:
        logger.info("用户中断，已处理部分结果未写入文件。")
        sys.exit(0)


if __name__ == "__main__":
    main()
