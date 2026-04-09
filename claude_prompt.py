"""
读取 aso_opportunities.csv，生成可直接粘贴给 Claude 的二次筛选 prompt。

用法:
    python claude_prompt.py              # 默认读 aso_opportunities.csv，取 Top 50
    python claude_prompt.py --top 30     # 只取 Top 30
    python claude_prompt.py --input results.csv --top 100
    python claude_prompt.py --out prompt.txt  # 将 prompt 写入文件而非打印到终端
"""

import argparse
import csv
import sys
from pathlib import Path


PROMPT_HEADER = """\
你是一位有 10 年经验的移动应用产品经理，擅长评估 App Store 市场机会。

以下是通过 Apple Autocomplete API + iTunes Search API 挖掘出的关键词机会列表，
已按「搜索量代理 / 竞争强度」得分从高到低排序。

每行格式：
  排名 | 关键词 | 机会分 | 补全排名 | 头部App评论数

请对**每一个**关键词完成以下四项分析：

1. **用户痛点**：这个搜索词背后，用户具体想解决什么问题？
2. **需求频率**：是「一次性需求」还是「高频刚需（每周/每天用）」？
3. **付费意愿**：用户是否有天然的付费动机？（订阅 / 买断 / 免费工具）
4. **综合优先级**：综合以上三点，给出 1-5 分（5=强烈推荐立即做，1=意义不大）

请以 Markdown 表格输出，列名为：
关键词 | 用户痛点 | 需求频率 | 付费意愿 | 优先级(1-5) | 理由（一句话）

---

关键词列表：

"""

PROMPT_FOOTER = """

---

完成表格后，请额外输出：
- **强烈推荐（优先级 4-5）的关键词汇总**，并说明为什么这些词适合做成独立 App
- **需要警惕的关键词**（机会分高但实际价值低的词），说明原因
"""


def load_top_keywords(csv_path: Path, top_n: int) -> list[dict]:
    """读取 CSV，返回前 top_n 条记录（已按 opportunity_score 降序排列）。"""
    if not csv_path.exists():
        print(f"错误：找不到文件 {csv_path}", file=sys.stderr)
        print("请先运行 python main.py 生成数据文件。", file=sys.stderr)
        sys.exit(1)

    rows = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    # 防御性排序（main.py 已排序，这里再确认一次）
    rows.sort(key=lambda r: float(r.get("opportunity_score", 0)), reverse=True)
    return rows[:top_n]


def format_keyword_table(rows: list[dict]) -> str:
    """将关键词列表格式化为 prompt 中间的文本块。"""
    lines = []
    for i, row in enumerate(rows, 1):
        keyword = row.get("keyword", "")
        score = float(row.get("opportunity_score", 0))
        rank = row.get("autocomplete_rank", "?")
        top_reviews = int(row.get("top_app_reviews", 0))
        lines.append(
            f"{i:>3}. {keyword:<35} "
            f"机会分={score:>6.2f}  "
            f"补全排名={rank:>2}  "
            f"头部App评论={top_reviews:>8,}"
        )
    return "\n".join(lines)


def build_prompt(rows: list[dict]) -> str:
    table = format_keyword_table(rows)
    return PROMPT_HEADER + table + PROMPT_FOOTER


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Claude 二次筛选 prompt")
    parser.add_argument(
        "--input",
        type=str,
        default="aso_opportunities.csv",
        metavar="FILE",
        help="输入 CSV 文件路径（默认：aso_opportunities.csv）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=50,
        metavar="N",
        help="取 Top N 个关键词（默认：50）",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        metavar="FILE",
        help="将 prompt 写入文件（不指定则打印到终端）",
    )
    args = parser.parse_args()

    csv_path = Path(args.input)
    rows = load_top_keywords(csv_path, args.top)

    if not rows:
        print("CSV 文件为空，没有可处理的关键词。", file=sys.stderr)
        sys.exit(1)

    prompt = build_prompt(rows)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(prompt, encoding="utf-8")
        print(f"Prompt 已写入 {out_path}（共 {len(rows)} 个关键词）")
    else:
        print(prompt)
        print(f"\n# 以上 prompt 包含 {len(rows)} 个关键词，可直接复制粘贴给 Claude。")


if __name__ == "__main__":
    main()
