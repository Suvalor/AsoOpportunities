"""
ASO 关键词机会挖掘工具 — 主流程

用法:
    python main.py               # 完整跑所有 240 个种子词（约 40 分钟）
    python main.py --seeds 20    # 只跑前 N 个种子（测试用）
    python main.py --country gb  # 指定市场区域
    python main.py --out results.csv  # 自定义输出文件名
"""

import argparse
import csv
import logging
import sys
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

OUTPUT_FIELDS = [
    "seed",
    "keyword",
    "autocomplete_rank",
    "top_app_reviews",
    "avg_reviews",
    "avg_rating",
    "result_count",
    "opportunity_score",
]


def run(seeds: list[str], country: str, out_path: Path) -> None:
    """
    主流程：
      1. 遍历种子词，调用 Autocomplete API 拿到补全词列表
      2. 对每个补全词查询 iTunes API 拿竞争数据
      3. 计算机会分
      4. 去重（同关键词保留最高分）
      5. 排序后写 CSV
    """
    # key: 关键词小写 → 已记录的最高分记录
    best: dict[str, dict] = {}

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
            }

            key = keyword.lower().strip()
            if key not in best or score > best[key]["opportunity_score"]:
                best[key] = record

    if not best:
        logger.warning("没有找到任何关键词，请检查网络连接或种子词配置。")
        return

    results = sorted(best.values(), key=lambda r: r["opportunity_score"], reverse=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    logger.info("结果已写入 %s（共 %d 条）", out_path, len(results))

    print("\n--- Top 10 关键词机会 ---")
    for i, r in enumerate(results[:10], 1):
        print(
            f"{i:>2}. [{r['opportunity_score']:>6.2f}分] "
            f"{r['keyword']!r:<35} "
            f"种子={r['seed']!r}  "
            f"补全排名={r['autocomplete_rank']}  "
            f"头部App评论={r['top_app_reviews']:,}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="ASO 关键词机会挖掘工具")
    parser.add_argument(
        "--seeds",
        type=int,
        default=None,
        metavar="N",
        help="只跑前 N 个种子词（不指定则跑全部 %(default)s 个）",
    )
    parser.add_argument(
        "--country",
        type=str,
        default=None,
        metavar="CODE",
        help="市场区域代码，默认使用 config.py 中的 COUNTRY",
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

    # country 优先用命令行参数，否则用 config.py 默认值
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
