"""
蓝海关键词综合评分（唯一实现，整数分，与 MySQL 列类型一致）。

算法 v2：非线性连续衰减 + 维度协同，替代 v1 的线性阈值跳变。
"""

from __future__ import annotations

import math


def blue_ocean_score(record: dict) -> tuple[int, str]:
    """
    输入一条 result 字典，返回 (score, flags_str)。

    评分维度（满分约 132 分）：
      1. 竞争强度（0-40）：指数连续衰减，核心维度
      2. 搜索量真实性（0-20）：coverage 连续函数
      3. 市场分散度（0-15）：concentration 连续函数
      4. 竞品老化（0-10）：竞品越久没更新越好
      5. 趋势信号（0-15）：跨国梯度 + 排名变化
      6. 跨平台信号（0-12）
      7. Google Trends 上升（0-8）
      8. Reddit 需求验证（0-6）
      9. Android 竞争修正（-10 ~ +6）
     10. 维度协同加成（0-8）
    """
    flags: list[str] = []

    # ── 1. 竞争强度（0-40）：指数连续衰减，核心维度 ──
    top_rev = max(record.get("top_app_reviews", 0), 1)
    competition = 40 * math.exp(-0.0004 * top_rev)
    if top_rev < 500:
        flags.append("头部极弱")
    elif top_rev < 5000:
        flags.append("竞争低")

    # ── 2. 搜索量真实性（0-20）：coverage 用连续函数 ──
    # coverage 高 = 多个种子都命中 = 搜索意图真实，边际递减
    coverage = record.get("seed_coverage", 0)
    search_auth = 20 * (1 - math.exp(-0.5 * coverage))
    if coverage >= 3:
        flags.append("多路径触发")

    # ── 3. 市场分散度（0-15）：连续函数替代阈值 ──
    conc = record.get("concentration", 1.0)
    if conc is None:
        conc = 1.0
    dispersion = 15 * max(0, 1 - conc)
    if conc < 0.3:
        flags.append("市场分散")

    # ── 4. 竞品老化（0-10）：连续函数 ──
    age = record.get("avg_update_age_months", 0) or 0
    staleness = min(10, age * 0.5)
    if age > 12:
        flags.append("竞品躺平")

    # ── 5. 趋势信号（0-15） ──
    trend_gap = record.get("trend_gap", 0) or 0
    rank_change = record.get("rank_change", 0) or 0
    trend_signal = min(15, max(0, trend_gap * 3 + rank_change * 1.5))
    if trend_gap > 3:
        flags.append("US领先趋势")
    if rank_change > 2:
        flags.append("排名上升")

    # ── 6. 跨平台信号（0-12） ──
    cross_platform_bonus = 12 if record.get("cross_platform") else 0
    if record.get("cross_platform"):
        flags.append("📱 双平台需求")

    # ── 7. Google Trends 上升（0-8） ──
    trends_rising_bonus = 8 if record.get("trends_rising") else 0
    if record.get("trends_rising"):
        flags.append("📈 Google趋势上升")

    # ── 8. Reddit 需求验证（0-6） ──
    reddit_count = record.get("reddit_post_count", 0) or 0
    reddit_bonus = min(6, reddit_count * 1.5)
    if reddit_count >= 5:
        flags.append("💬 Reddit有需求讨论")

    # ── 9. Android 竞争修正（-10 ~ +6） ──
    installs = record.get("gplay_top_installs_num", 0) or 0
    if installs > 1_000_000:
        gplay_mod = -10
        flags.append("🔴 Android头部安装量过高")
    elif 0 < installs < 10_000:
        gplay_mod = 6
        flags.append("💎 Android竞争极弱")
    else:
        gplay_mod = 0

    # ── 10. 维度协同加成 ──
    synergy = 0
    # 低竞争 + 跨平台 = 强信号
    if competition >= 25 and record.get("cross_platform"):
        synergy += 5
        flags.append("低竞争+跨平台协同")
    # 低竞争 + 市场分散 = 空白市场
    if competition >= 25 and conc < 0.3:
        synergy += 3
        flags.append("低竞争+分散市场协同")

    score = int(
        competition + search_auth + dispersion + staleness
        + trend_signal + cross_platform_bonus + trends_rising_bonus
        + reddit_bonus + gplay_mod + synergy
    )

    return max(score, 0), " | ".join(flags)


def blue_ocean_label(score: int) -> str:
    """
    分数 → 标签映射。

    阈值 75/55/35 基于 v2 算法模拟验证：
    💎~8%, 🟢~25%, 🟡~35%, 🔴~31%（健康金字塔分布）。
    """
    if score >= 75:
        return "💎 金矿"
    if score >= 55:
        return "🟢 蓝海"
    if score >= 35:
        return "🟡 观察"
    return "🔴 跳过"
