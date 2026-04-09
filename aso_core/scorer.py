"""
蓝海关键词综合评分（唯一实现，整数分，与 MySQL 列类型一致）。
"""


def blue_ocean_score(record: dict) -> tuple[int, str]:
    """
    输入一条 result 字典，返回 (score, flags_str)。

    评分规则（满分 110 分）：
      搜索量真实性（最高30分）
      趋势与竞争强度等见代码分支。
    """
    score = 0
    flags: list[str] = []

    coverage = record.get("seed_coverage", 1)
    if coverage >= 3:
        score += 30
        flags.append("多路径触发")
    elif coverage == 2:
        score += 15

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


def blue_ocean_label(score: int) -> str:
    if score >= 80:
        return "💎 金矿"
    if score >= 60:
        return "🟢 蓝海"
    if score >= 40:
        return "🟡 观察"
    return "🔴 跳过"
