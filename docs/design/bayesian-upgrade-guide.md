# ASO 贝叶斯统计算法升级指导

---

## 1. 多维联合后验更新

从各维度独立的 Beta/Normal 共轭升级为 **Normal-Inverse-Wishart (NIW) 联合共轭族**：

- 先验: Σ ~ IW(Λ₀, ν₀), μ | Σ ~ N(μ₀, Σ/κ₀)
- 后验: Σ | X ~ IW(Λₙ, νₙ), μ | Σ ~ N(μₙ, Σ/κₙ)
- 后验预测分布: 多元 t 分布，自然包含维度间相关性

**关键参数更新：**
- κₙ = κ₀ + n
- νₙ = ν₀ + n
- μₙ = (κ₀μ₀ + nX̄) / κₙ
- Λₙ = Λ₀ + S + (κ₀n/κₙ)(X̄ - μ₀)(X̄ - μ₀)ᵀ

其中 S = Σᵢ(xᵢ - X̄)(xᵢ - X̄)ᵀ 是散布矩阵。

---

## 2. 新维度先验选择

### commercial_value (0-10)
- **先验**: Beta(3, 7) 缩放到 [0, 10]
- **理由**: 右偏分布，反映大多数关键词变现能力平庸的先验信念
- **有效样本量**: 5-10，弱信息先验，数据积累后后验快速接管

### long_tail_potential (0-8)
- **先验**: Gamma(2, 0.5) 截断到 [0, 8]
- **理由**: 非负右偏，反映长尾价值从零指数增长的业务直觉
- **有效样本量**: 5-10

---

## 3. 协方差矩阵估计

**三阶段策略：**

| 阶段 | 方法 | 适用场景 |
|------|------|----------|
| v3.1 | Ledoit-Wolf 收缩估计器 | 保证正定，小样本稳定 |
| v3.5 | NIW 在线 Λ 更新 | 与联合共轭框架一体 |
| v4 | PyMC + LKJ 先验 | 精确推断，需 MCMC |

**Ledoit-Wolf 收缩目标**：单位矩阵 scaled by trace
- Σ̂ = (1-α)S + α(tr(S)/p)I
- α 通过 Oracle Approximating Shrinkage (OAS) 自动选择

---

## 4. 可信区间改进

**三个层次：**

| 场景 | 方法 | 精度 | 开销 |
|------|------|------|------|
| 日常评分 | 协方差感知 + Delta method | 中 | O(p²) |
| 仪表盘展示 | Beta 精确分位数替代正态近似 | 高 | O(1) |
| 报告生成 | Monte Carlo 抽样 + HDI | 最高 | O(N×p) |

**HDI (Highest Density Interval)** 替代 ETI (Equal-Tailed Interval)：
- 对右偏后验更准确
- 保证区间内每个点的密度 >= 区间外任何点
- 实现方式：从后验抽样后，找最短包含 (1-α)% 样本的区间

---

## 5. 维度贡献精确计算

**当前问题**：`_estimate_decay_rates` 用 `score/40` 估算 competition decay rate，但 score 是多维加总。

**修复方案**：从 record 字段直接计算各维度贡献，不从总分反推。

```python
def _exact_dimension_contribution(dim: str, record: dict, priors: dict) -> float:
    """精确计算单维度贡献值，用于后验更新。"""
    if dim == "competition_weight":
        comp_w = _posterior_mean_weight("competition_weight", priors)
        comp_k = _posterior_mean_decay("competition_decay_rate", priors)
        top_rev = max(record.get("top_app_reviews", 0), 1)
        return comp_w * math.exp(-comp_k * top_rev)
    # ... 其他维度直接从 record 字段计算
```

---

## 6. 实施建议

1. **Phase 1**: 先修复 `_estimate_decay_rates` 和 `_dimension_contribution` 的精确计算
2. **Phase 2**: 引入 Ledoit-Wolf 收缩估计器替代独立方差假设
3. **Phase 3**: 新增 commercial_value 和 long_tail 维度的先验和后验更新
4. **Phase 4**: 可选升级到 NIW 联合共轭（需要足够数据量 n >> p）
