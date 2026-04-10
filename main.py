"""
ASO 蓝海关键词分析系统 — CLI 入口

用法:
    python main.py               # 完整跑所有 240 个种子词（约 40 分钟）
    python main.py --seeds 20    # 只跑前 N 个种子（测试用）
    python main.py --country gb  # 仅扫描该国（覆盖 ASO_SCAN_COUNTRIES）
    python main.py --out results.csv  # 自定义输出文件名

配置优先级见 aso_core.settings；本地可在仓库根目录放置 .env 或 config.json。
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from aso_core.config_data import SEEDS
from aso_core.scanner import run_full_scan
from aso_core.scorer import blue_ocean_label, blue_ocean_score
from aso_core.settings import get_settings

load_dotenv(override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

OUTPUT_FIELDS = [
    "country",
    "seed",
    "keyword",
    "autocomplete_rank",
    "top_app_reviews",
    "avg_reviews",
    "avg_rating",
    "result_count",
    "opportunity_score",
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
        print(
            f"   [{int(r['blue_ocean_score']):>5d}] {r['keyword']}  |  {r['blue_ocean_flags']}"
        )

    print(f"\n🟢 蓝海（60-79分）：{len(blue)} 个")
    for r in blue:
        print(
            f"   [{int(r['blue_ocean_score']):>5d}] {r['keyword']}  |  {r['blue_ocean_flags']}"
        )

    print(f"\n🟡 观察（40-59分）：{len(watch)} 个")
    print(f"🔴 跳过（<40分）：{len(skip)} 个")

    status = "✅ 成功" if history_saved else "❌ 失败"
    print(f"\nrank_history 写入：{status}")
    print("=" * 60)


def run_cli(seeds_subset: list[str] | None, country: str | None, out_path: Path) -> None:
    """执行扫描、蓝海评分、写 CSV。"""
    scan_countries = [country] if country else None
    results = run_full_scan(countries=scan_countries, seeds=seeds_subset)
    if not results:
        logger.warning("没有找到任何关键词，请检查网络连接或种子词配置。")
        return

    rank_file = get_settings().rank_history_path
    history_saved = rank_file.exists()

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

    # 确认 rank 文件存在（run_full_scan 已写）
    if rank_file.exists():
        history_saved = True
    _print_summary(results, history_saved)


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
        help="仅扫描单个区域；不指定则使用环境变量 ASO_SCAN_COUNTRIES（见 aso_core.scanner）",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="aso_opportunities.csv",
        metavar="FILE",
        help="输出 CSV 文件路径（默认：aso_opportunities.csv）",
    )
    args = parser.parse_args()

    seeds_subset = SEEDS[: args.seeds] if args.seeds else None
    n = len(seeds_subset) if seeds_subset is not None else len(SEEDS)
    logger.info("共 %d 个种子词，预计约 %.0f 分钟", n, n * 20 * 0.5 / 60)

    country = args.country
    if country is not None:
        country = country.strip().lower()

    out_path = Path(args.out)

    try:
        run_cli(seeds_subset=seeds_subset, country=country, out_path=out_path)
    except KeyboardInterrupt:
        logger.info("用户中断，已处理部分结果未写入文件。")
        sys.exit(0)


if __name__ == "__main__":
    main()
