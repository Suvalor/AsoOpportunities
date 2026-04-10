"""
蓝海关键词综合评分（唯一实现，整数分，与 MySQL 列类型一致）。
"""


def blue_ocean_score(record: dict) -> tuple[int, str]:
    """
    输入一条 result 字典，返回 (score, flags_str)。

    评分规则（满分约 150 分）：
      搜索量真实性（最高30分）
      竞争强度（最高50分）
      趋势信号（最高20分）
      新增维度：跨平台/Trends/Reddit/安装量修正（最高+40/-20分）
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

    if record.get("cross_platform"):
        score += 15
        flags.append("📱 双平台需求")

    if record.get("trends_rising"):
        score += 15
        flags.append("📈 Google趋势上升")

    if record.get("reddit_post_count", 0) >= 5:
        score += 10
        flags.append("💬 Reddit有需求讨论")

    installs = record.get("gplay_top_installs_num", 0)
    if installs > 1_000_000:
        score -= 20
        flags.append("🔴 Android头部安装量过高")
    elif installs > 0 and installs < 10_000:
        score += 10
        flags.append("💎 Android竞争极弱")

    return score, " | ".join(flags)


def blue_ocean_label(score: int) -> str:
    if score >= 80:
        return "💎 金矿"
    if score >= 60:
        return "🟢 蓝海"
    if score >= 40:
        return "🟡 观察"
    return "🔴 跳过"
