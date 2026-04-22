"""
贝叶斯先验/后验管理：共轭更新、可信区间计算。

权重维度用 Beta-Bernoulli 共轭对（后验闭式更新）。
衰减率维度用 Normal-Normal 共轭对。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from aso_core.scorer import (
    PriorState,
    _DECAY_RATE_DEFAULTS,
    _DIMENSION_DEFAULTS,
)

from .database import get_all_priors, get_batch_label_stats, upsert_prior

logger = logging.getLogger(__name__)

# 弱信息先验的有效样本量（越大越接近硬编码默认值）
_PRIOR_EFFECTIVE_N = 20


def _default_priors() -> dict[str, PriorState]:
    """构造默认先验：Beta 均值 = 当前权重/最大值，有效样本量 = _PRIOR_EFFECTIVE_N。"""
    priors: dict[str, PriorState] = {}

    for dim, (weight, max_val) in _DIMENSION_DEFAULTS.items():
        # Beta 均值 = weight / max_val
        p = weight / max_val
        # Beta(alpha, beta) 均值 = alpha/(alpha+beta) = p
        # 设 alpha + beta = effective_n → alpha = p * n, beta = (1-p) * n
        alpha = max(p * _PRIOR_EFFECTIVE_N, 0.5)
        beta_param = max((1 - p) * _PRIOR_EFFECTIVE_N, 0.5)
        priors[dim] = PriorState(
            dimension=dim,
            alpha=alpha,
            beta_param=beta_param,
            mu=weight,  # mu 存原始权重值（用于显示）
            sigma_sq=0.0,
            n_obs=0,
        )

    for dim, (rate, var) in _DECAY_RATE_DEFAULTS.items():
        priors[dim] = PriorState(
            dimension=dim,
            alpha=1.0,
            beta_param=1.0,
            mu=rate,
            sigma_sq=var,
            n_obs=0,
        )

    return priors


def get_current_priors() -> dict[str, PriorState]:
    """从 DB 读取当前后验；若空则返回默认先验。"""
    rows = get_all_priors()
    if not rows:
        return _default_priors()

    priors: dict[str, PriorState] = {}
    for r in rows:
        priors[r["dimension"]] = PriorState(
            dimension=r["dimension"],
            alpha=float(r["alpha"]),
            beta_param=float(r["beta_param"]),
            mu=float(r["mu"]),
            sigma_sq=float(r["sigma_sq"]),
            n_obs=int(r["n_obs"]),
        )

    # 补齐缺失维度（新增维度时 DB 中尚无记录）
    defaults = _default_priors()
    for dim in defaults:
        if dim not in priors:
            priors[dim] = defaults[dim]

    return priors


def update_posteriors(batch_id: str) -> dict:
    """
    全量扫描后，基于本批次关键词的标签结果更新各维度后验。

    逻辑：
    - 对每个权重维度，统计「该维度贡献高 且 标签为 Gold/Blue」的成功次数，
      用 Beta-Bernoulli 共轭更新 alpha/beta。
    - 对每个衰减率维度，用 Normal-Normal 共轭更新 mu/sigma_sq。
    """
    rows = get_batch_label_stats(batch_id)
    if not rows:
        logger.info("update_posteriors: 批次 %s 无关键词数据，跳过", batch_id)
        return {"updated": 0}

    priors = get_current_priors()
    changes: dict[str, dict] = {}

    # 统计各维度的成功/失败
    n = len(rows)
    for dim, (weight, max_val) in _DIMENSION_DEFAULTS.items():
        successes = 0
        for r in rows:
            label = r.get("blue_ocean_label") or ""
            score = int(r.get("blue_ocean_score") or 0)
            # Gold/Blue = 成功
            is_good = score >= 55
            # 该维度贡献是否高于中位数
            contribution = _dimension_contribution(dim, r)
            threshold = weight * 0.5
            is_high = contribution >= threshold
            # 成功 = 高贡献 且 好标签
            if is_high and is_good:
                successes += 1

        p = priors.get(dim)
        if p is None:
            continue
        old_alpha, old_beta = p.alpha, p.beta_param
        # Beta-Bernoulli 共轭更新
        new_alpha = old_alpha + successes
        new_beta = old_beta + (n - successes)
        new_n_obs = p.n_obs + n

        upsert_prior(dim, new_alpha, new_beta, p.mu, p.sigma_sq, new_n_obs)
        changes[dim] = {
            "old_alpha": round(old_alpha, 2),
            "new_alpha": round(new_alpha, 2),
            "old_beta": round(old_beta, 2),
            "new_beta": round(new_beta, 2),
            "successes": successes,
            "total": n,
        }

    # 衰减率维度：用 Normal-Normal 共轭
    for dim, (default_rate, default_var) in _DECAY_RATE_DEFAULTS.items():
        # 从数据中估计经验衰减率
        empirical_rates = _estimate_decay_rates(dim, rows)
        if not empirical_rates:
            continue
        p = priors.get(dim)
        if p is None:
            continue

        # Normal-Normal 共轭：先验 N(mu0, sigma0_sq), 数据均值 x_bar, 数据方差 s_sq
        x_bar = sum(empirical_rates) / len(empirical_rates)
        m = len(empirical_rates)
        s_sq = sum((x - x_bar) ** 2 for x in empirical_rates) / max(m, 1)

        old_mu, old_sigma_sq = p.mu, p.sigma_sq
        # 后验精度 = 先验精度 + 数据精度
        prior_prec = 1.0 / old_sigma_sq if old_sigma_sq > 0 else 0
        data_prec = m / s_sq if s_sq > 0 else 1e6
        post_prec = prior_prec + data_prec
        new_sigma_sq = 1.0 / post_prec
        new_mu = (prior_prec * old_mu + data_prec * x_bar) / post_prec
        new_n_obs = p.n_obs + m

        upsert_prior(dim, p.alpha, p.beta_param, new_mu, new_sigma_sq, new_n_obs)
        changes[dim] = {
            "old_mu": round(old_mu, 6),
            "new_mu": round(new_mu, 6),
            "old_sigma_sq": round(old_sigma_sq, 8),
            "new_sigma_sq": round(new_sigma_sq, 8),
            "n_data_points": m,
        }

    logger.info(
        "update_posteriors batch_id=%s 更新 %d 个维度，%d 条关键词",
        batch_id, len(changes), n,
    )
    return {"updated": len(changes), "changes": changes, "n_keywords": n}


def _dimension_contribution(dim: str, r: dict) -> float:
    """计算单条关键词在指定维度的贡献值。"""
    if dim == "competition_weight":
        top_rev = max(int(r.get("top_reviews") or 0), 1)
        return 40 * math.exp(-0.0004 * top_rev)
    elif dim == "search_auth_weight":
        coverage = int(r.get("seed_coverage") or 0)
        return 20 * (1 - math.exp(-0.5 * coverage))
    elif dim == "dispersion_weight":
        conc = r.get("concentration")
        conc = conc if conc is not None else 1.0
        return 15 * max(0, 1 - conc)
    elif dim == "staleness_weight":
        age = float(r.get("avg_update_age_months") or 0)
        return min(10, age * 0.5)
    elif dim == "trend_signal_weight":
        trend_gap = float(r.get("trend_gap") or 0)
        rank_change = int(r.get("rank_change") or 0)
        return min(15, max(0, trend_gap * 3 + rank_change * 1.5))
    elif dim == "cross_platform_weight":
        return 12 if r.get("cross_platform") else 0
    elif dim == "trends_rising_weight":
        return 8 if r.get("trends_rising") else 0
    elif dim == "reddit_weight":
        reddit_count = int(r.get("reddit_post_count") or 0)
        return min(6, reddit_count * 1.5)
    elif dim == "gplay_mod_weight":
        installs = int(r.get("gplay_top_installs_num") or 0)
        if installs > 1_000_000:
            return -10
        elif 0 < installs < 10_000:
            return 6
        return 0
    elif dim == "synergy_weight":
        # 协同加成需要多维度联合判断，简化为 0
        return 0
    return 0


def _estimate_decay_rates(dim: str, rows: list[dict]) -> list[float]:
    """从数据中估计衰减率的经验值。"""
    rates: list[float] = []
    for r in rows:
        score = int(r.get("blue_ocean_score") or 0)
        if score < 10:
            continue
        if dim == "competition_decay_rate":
            top_rev = int(r.get("top_reviews") or 0)
            if top_rev > 100:
                # competition = 40 * exp(-k * top_rev)
                # score ≈ competition + other_dims → k ≈ -ln(score/40) / top_rev
                ratio = min(score / 40.0, 0.99)
                if ratio > 0.01:
                    rates.append(-math.log(ratio) / top_rev)
        elif dim == "search_auth_decay_rate":
            coverage = int(r.get("seed_coverage") or 0)
            if coverage >= 2:
                # search_auth = 20 * (1 - exp(-k * coverage))
                ratio = max(1 - score / 20.0, 0.01)
                if ratio < 0.99:
                    rates.append(-math.log(ratio) / coverage)
    return rates


def compute_credible_interval(
    priors: dict[str, PriorState],
    dimension: str,
    ci: float = 0.95,
) -> tuple[float, float]:
    """计算指定维度的可信区间。

    权重维度：Beta 后验 → 用正态近似。
    衰减率维度：Normal 后验 → 直接用 mu ± z * sigma。
    """
    p = priors.get(dimension)
    if p is None:
        return (0.0, 0.0)

    z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(ci, 1.96)

    if dimension in _DIMENSION_DEFAULTS:
        # Beta 后验 → 正态近似
        a, b = p.alpha, p.beta_param
        mean = a / (a + b)
        var = (a * b) / ((a + b) ** 2 * (a + b + 1))
        max_val = _DIMENSION_DEFAULTS[dimension][1]
        lower = max(0, (mean - z * math.sqrt(var)) * max_val)
        upper = min(max_val, (mean + z * math.sqrt(var)) * max_val)
        return (round(lower, 2), round(upper, 2))
    elif dimension in _DECAY_RATE_DEFAULTS:
        # Normal 后验
        lower = p.mu - z * math.sqrt(max(p.sigma_sq, 0))
        upper = p.mu + z * math.sqrt(max(p.sigma_sq, 0))
        return (round(lower, 6), round(upper, 6))

    return (0.0, 0.0)
