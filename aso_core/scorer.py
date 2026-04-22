"""
蓝海关键词综合评分（唯一实现，整数分，与 MySQL 列类型一致）。

算法 v2：非线性连续衰减 + 维度协同，替代 v1 的线性阈值跳变。
算法 v3：贝叶斯增强 — 维度权重从后验分布读取，输出含 95% 可信区间。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


# ════════════════════════════════════════════════════════════
#  先验状态（在此定义避免循环导入）
# ════════════════════════════════════════════════════════════

@dataclass
class PriorState:
    """单维度贝叶斯先验/后验状态。"""
    dimension: str
    # Beta 后验超参数（权重维度）
    alpha: float = 1.0
    beta_param: float = 1.0
    # Normal 后验超参数（衰减率维度）
    mu: float = 0.0
    sigma_sq: float = 1.0
    # 观测数
    n_obs: int = 0


# ════════════════════════════════════════════════════════════
#  维度默认值
# ════════════════════════════════════════════════════════════

# 权重维度 → (默认权重, 最大值)
_DIMENSION_DEFAULTS: dict[str, tuple[float, float]] = {
    "competition_weight":      (40.0, 40.0),
    "search_auth_weight":      (20.0, 20.0),
    "dispersion_weight":       (15.0, 15.0),
    "staleness_weight":        (10.0, 10.0),
    "trend_signal_weight":     (15.0, 15.0),
    "cross_platform_weight":   (12.0, 12.0),
    "trends_rising_weight":    (8.0,  8.0),
    "reddit_weight":           (6.0,  6.0),
    "gplay_mod_weight":        (10.0, 10.0),
    "synergy_weight":          (8.0,  8.0),
}

# 衰减率维度 → (当前默认值, 初始方差)
_DECAY_RATE_DEFAULTS: dict[str, tuple[float, float]] = {
    "competition_decay_rate": (0.0004, 1e-8),
    "search_auth_decay_rate": (0.5,    1e-4),
}


# ════════════════════════════════════════════════════════════
#  v2: 原始硬编码评分（保持不变，向后兼容）
# ════════════════════════════════════════════════════════════

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
    if competition >= 25 and record.get("cross_platform"):
        synergy += 5
        flags.append("低竞争+跨平台协同")
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


# ════════════════════════════════════════════════════════════
#  v3: 贝叶斯增强评分
# ════════════════════════════════════════════════════════════

def _posterior_mean_weight(dim: str, priors: dict[str, PriorState]) -> float:
    """从 Beta 后验取维度权重的均值；无先验时回退到硬编码默认值。"""
    if dim in priors:
        p = priors[dim]
        return p.alpha / (p.alpha + p.beta_param) * _DIMENSION_DEFAULTS[dim][1]
    return _DIMENSION_DEFAULTS[dim][0]


def _posterior_mean_decay(dim: str, priors: dict[str, PriorState]) -> float:
    """从 Normal 后验取衰减率的均值；无先验时回退到硬编码默认值。"""
    if dim in priors:
        return priors[dim].mu
    return _DECAY_RATE_DEFAULTS[dim][0]


def _posterior_weight_variance(dim: str, priors: dict[str, PriorState]) -> float:
    """Beta 后验方差 × max²，用于可信区间传播。"""
    if dim in priors:
        p = priors[dim]
        a, b = p.alpha, p.beta_param
        var = (a * b) / ((a + b) ** 2 * (a + b + 1))
        return var * _DIMENSION_DEFAULTS[dim][1] ** 2
    max_w = _DIMENSION_DEFAULTS[dim][1]
    return (max_w / 4) ** 2


def _posterior_decay_variance(dim: str, priors: dict[str, PriorState]) -> float:
    """Normal 后验方差，用于衰减率的不确定性传播。"""
    if dim in priors:
        return priors[dim].sigma_sq
    return _DECAY_RATE_DEFAULTS[dim][1]


def blue_ocean_score_bayesian(
    record: dict,
    priors: dict[str, PriorState] | None = None,
) -> tuple[int, str, int, int]:
    """贝叶斯增强版蓝海评分。

    返回 (score, flags_str, ci_lower, ci_upper)。
    - score: 整数分（与 v2 一致）
    - flags_str: 标记字符串
    - ci_lower, ci_upper: 95% 可信区间（整数）

    若 priors 为 None 或空，回退到 v2 硬编码权重，CI 退化为 ±0。
    """
    if not priors:
        score, flags = blue_ocean_score(record)
        return score, flags, score, score

    flags: list[str] = []

    # ── 1. 竞争强度 ──────────────────────────────────────
    comp_w = _posterior_mean_weight("competition_weight", priors)
    comp_k = _posterior_mean_decay("competition_decay_rate", priors)
    top_rev = max(record.get("top_app_reviews", 0), 1)
    competition = comp_w * math.exp(-comp_k * top_rev)
    if top_rev < 500:
        flags.append("头部极弱")
    elif top_rev < 5000:
        flags.append("竞争低")

    # ── 2. 搜索量真实性 ──────────────────────────────────
    sa_w = _posterior_mean_weight("search_auth_weight", priors)
    sa_k = _posterior_mean_decay("search_auth_decay_rate", priors)
    coverage = record.get("seed_coverage", 0)
    search_auth = sa_w * (1 - math.exp(-sa_k * coverage))
    if coverage >= 3:
        flags.append("多路径触发")

    # ── 3. 市场分散度 ────────────────────────────────────
    disp_w = _posterior_mean_weight("dispersion_weight", priors)
    conc = record.get("concentration", 1.0)
    if conc is None:
        conc = 1.0
    dispersion = disp_w * max(0, 1 - conc)
    if conc < 0.3:
        flags.append("市场分散")

    # ── 4. 竞品老化 ─────────────────────────────────────
    stal_w = _posterior_mean_weight("staleness_weight", priors)
    age = record.get("avg_update_age_months", 0) or 0
    staleness = min(stal_w, age * 0.5)
    if age > 12:
        flags.append("竞品躺平")

    # ── 5. 趋势信号 ─────────────────────────────────────
    trend_w = _posterior_mean_weight("trend_signal_weight", priors)
    trend_gap = record.get("trend_gap", 0) or 0
    rank_change = record.get("rank_change", 0) or 0
    trend_signal = min(trend_w, max(0, trend_gap * 3 + rank_change * 1.5))
    if trend_gap > 3:
        flags.append("US领先趋势")
    if rank_change > 2:
        flags.append("排名上升")

    # ── 6. 跨平台信号 ───────────────────────────────────
    cp_w = _posterior_mean_weight("cross_platform_weight", priors)
    cross_platform_bonus = cp_w if record.get("cross_platform") else 0
    if record.get("cross_platform"):
        flags.append("📱 双平台需求")

    # ── 7. Google Trends 上升 ────────────────────────────
    tr_w = _posterior_mean_weight("trends_rising_weight", priors)
    trends_rising_bonus = tr_w if record.get("trends_rising") else 0
    if record.get("trends_rising"):
        flags.append("📈 Google趋势上升")

    # ── 8. Reddit 需求验证 ──────────────────────────────
    rd_w = _posterior_mean_weight("reddit_weight", priors)
    reddit_count = record.get("reddit_post_count", 0) or 0
    reddit_bonus = min(rd_w, reddit_count * 1.5)
    if reddit_count >= 5:
        flags.append("💬 Reddit有需求讨论")

    # ── 9. Android 竞争修正 ──────────────────────────────
    gp_w = _posterior_mean_weight("gplay_mod_weight", priors)
    installs = record.get("gplay_top_installs_num", 0) or 0
    if installs > 1_000_000:
        gplay_mod = -gp_w
        flags.append("🔴 Android头部安装量过高")
    elif 0 < installs < 10_000:
        gplay_mod = gp_w * 0.6
        flags.append("💎 Android竞争极弱")
    else:
        gplay_mod = 0

    # ── 10. 维度协同加成 ────────────────────────────────
    syn_w = _posterior_mean_weight("synergy_weight", priors)
    synergy = 0
    if competition >= comp_w * 0.625 and record.get("cross_platform"):
        synergy += syn_w * 0.625
        flags.append("低竞争+跨平台协同")
    if competition >= comp_w * 0.625 and conc < 0.3:
        synergy += syn_w * 0.375
        flags.append("低竞争+分散市场协同")

    # ── 总分 ────────────────────────────────────────────
    score = int(round(
        competition + search_auth + dispersion + staleness
        + trend_signal + cross_platform_bonus + trends_rising_bonus
        + reddit_bonus + gplay_mod + synergy
    ))
    score = max(0, min(150, score))

    # ── 可信区间（方差求和 + 1.96σ） ────────────────────
    var_total = (
        _posterior_weight_variance("competition_weight", priors)
        + _posterior_decay_variance("competition_decay_rate", priors) * (top_rev ** 2)
        + _posterior_weight_variance("search_auth_weight", priors)
        + _posterior_decay_variance("search_auth_decay_rate", priors)
        + _posterior_weight_variance("dispersion_weight", priors)
        + _posterior_weight_variance("staleness_weight", priors)
        + _posterior_weight_variance("trend_signal_weight", priors)
        + _posterior_weight_variance("cross_platform_weight", priors)
        + _posterior_weight_variance("trends_rising_weight", priors)
        + _posterior_weight_variance("reddit_weight", priors)
        + _posterior_weight_variance("gplay_mod_weight", priors)
        + _posterior_weight_variance("synergy_weight", priors)
    )
    ci_half = 1.96 * math.sqrt(var_total)
    ci_lower = max(0, int(round(score - ci_half)))
    ci_upper = min(150, int(round(score + ci_half)))

    return score, " | ".join(flags), ci_lower, ci_upper