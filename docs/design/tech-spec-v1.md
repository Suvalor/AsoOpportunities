# ASO 核心算法升级 - 技术方案 v1

> 版本: 1.0.0 | 日期: 2026-04-26 | 状态: 设计阶段

---

## 1. scorer.py v4 算法设计

### 1.1 新维度函数签名

```python
def _commercial_value(record: dict) -> tuple[float, str]:
    """
    商业价值维度（0-20）：评估关键词的商业变现潜力。

    输入字段：
      - top_reviews: int            -- 头部 App 评论数
      - cross_platform: bool        -- 是否双平台出现
      - trends_rising: bool         -- Google Trends 是否上升
      - concentration: float        -- 市场集中度
      - search_volume_tier: int     -- 搜索量级分层（1-5）

    计算逻辑：
      base = 8
      if search_volume_tier >= 3: base += 4
      if concentration < 0.4: base += 3
      if top_reviews < 2000: base += 3
      if cross_platform and trends_rising: base += 2

    返回：(min(20, base), flag_str)
    """


def _long_tail_potential(record: dict) -> tuple[float, str]:
    """
    长尾潜力维度（0-15）：评估关键词作为长尾词的挖掘价值。

    输入字段：
      - seed_coverage: int          -- 种子覆盖数
      - autocomplete_rank: int      -- 补全排名
      - top_reviews: int            -- 头部 App 评论数
      - concentration: float        -- 市场集中度
      - search_volume_tier: int     -- 搜索量级分层

    计算逻辑：
      base = 3
      if autocomplete_rank >= 5: base += 3
      if seed_coverage >= 2: base += 3
      if top_reviews < 5000 and concentration < 0.5: base += 3
      if search_volume_tier in (2, 3): base += 3

    返回：(min(15, base), flag_str)
    """
```

### 1.2 权重分配方案

v4 新满分 200，较 v2/v3 的 ~132 提升 51.5%：

| # | 维度 | v2/v3 权重 | v4 权重 | v4 满分 | 调整理由 |
|---|------|-----------|---------|---------|---------|
| 1 | competition | 40 | 35 | 35 | 商业价值维度分担了部分竞争判断 |
| 2 | search_auth | 20 | 18 | 18 | seed_coverage 在长尾维度也有贡献 |
| 3 | dispersion | 15 | 12 | 12 | concentration 在商业价值维度复用 |
| 4 | staleness | 10 | 10 | 10 | 不变 |
| 5 | trend_signal | 15 | 12 | 12 | trends_rising 在商业价值维度有交叉 |
| 6 | cross_platform | 12 | 10 | 10 | 交叉贡献已计入商业价值 |
| 7 | trends_rising | 8 | 8 | 8 | 不变 |
| 8 | reddit | 6 | 6 | 6 | 不变 |
| 9 | gplay_mod | -10~+10 | -10~+8 | 8 | 略收正向上限 |
| 10 | synergy | 8 | 6 | 6 | 协同部分由新维度替代 |
| **11** | **commercial_value** | **0** | **20** | **20** | **新增** |
| **12** | **long_tail_potential** | **0** | **15** | **15** | **新增** |
| | **合计** | **~132** | | **200** | |

```python
_DIMENSION_DEFAULTS_V4: dict[str, tuple[float, float]] = {
    "competition_weight":      (35.0, 35.0),
    "search_auth_weight":      (18.0, 18.0),
    "dispersion_weight":       (12.0, 12.0),
    "staleness_weight":        (10.0, 10.0),
    "trend_signal_weight":     (12.0, 12.0),
    "cross_platform_weight":   (10.0, 10.0),
    "trends_rising_weight":    (8.0,  8.0),
    "reddit_weight":           (6.0,  6.0),
    "gplay_mod_weight":        (8.0,  8.0),
    "synergy_weight":          (6.0,  6.0),
    "commercial_value_weight": (20.0, 20.0),
    "long_tail_potential_weight": (15.0, 15.0),
}
```

### 1.3 向后兼容策略

v2/v3 函数保留不动，v4 作为独立新函数：

```python
_SCORER_VERSION = int(os.getenv("ASO_SCORER_VERSION", "4"))

def get_scorer():
    if _SCORER_VERSION == 4:
        return blue_ocean_score_v4
    elif _SCORER_VERSION == 3:
        return blue_ocean_score_bayesian
    else:
        return blue_ocean_score
```

v4 标签阈值：100/70/40（基于 200 满分体系）。

### 1.4 贝叶斯 v4 改进点

- `commercial_value_weight` -- Beta-Bernoulli 共轭，先验有效样本量 20
- `long_tail_potential_weight` -- Beta-Bernoulli 共轭，先验有效样本量 20
- CI 上限从 150 改为 200

---

## 2. trends.py 增强方案

### 2.1 搜索量级获取

新增 `get_trends_interest_over_time()` 函数：

```python
def get_trends_interest_over_time(
    keyword: str,
    timeframe: str | None = None,
    geo: str = "US",
) -> dict:
    """
    返回：
    {
        "timeline": list[dict],  -- [{date: str, value: float}, ...]
        "avg_interest": float,   -- 平均兴趣值 (0-100)
        "volume_tier": int,      -- 搜索量级分层 (1-5)
        "slope": float,          -- 趋势斜率
        "slope_segments": list,  -- 分段斜率
    }

    volume_tier: 5=avg>=75, 4=avg>=50, 3=avg>=25, 2=avg>=10, 1=avg>0, 0=缺失
    """
```

### 2.2 趋势曲线斜率计算

全局线性回归（最小二乘法）+ 分段斜率（每 30 天一段）：

```python
def _compute_slope(timeline: list[dict]) -> float:
    if len(timeline) < 2:
        return 0.0
    n = len(timeline)
    x = list(range(n))
    y = [p["value"] for p in timeline]
    x_mean, y_mean = sum(x) / n, sum(y) / n
    numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    denominator = sum((xi - x_mean) ** 2 for xi in x)
    return numerator / denominator if denominator else 0.0
```

### 2.3 Rate Limiting 策略

- 请求间隔：`TRENDS_IOT_SLEEP` 环境变量，默认 1.5s
- 批量间隔：每 5 次请求后额外 sleep 5s
- 429 重试：指数退避（5/10/20s），最多 3 次

```python
_TRENDS_IOT_SLEEP = max(float(os.getenv("TRENDS_IOT_SLEEP", "1.5")), 1.0)
_MAX_RETRIES_429 = 3
```

### 2.4 数据结构设计

新增输出字段（写入 record dict）：

| 字段名 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `search_volume_tier` | `int` | 搜索量级分层 1-5 | 0 |
| `trends_avg_interest` | `float` | 平均兴趣值 | 0.0 |
| `trends_slope` | `float` | 全局趋势斜率 | 0.0 |
| `trends_slope_latest` | `float` | 最近一段斜率 | 0.0 |

scanner.py 调用变更：所有关键词都查 interest over time（不再限制 rank <= 10）。

---

## 3. bayesian_updater.py 重构方案

### 3.1 维度贡献精确计算

从 record 字段直接计算各维度贡献值，不从总分反推：

```python
def _dimension_contribution_v4(dim: str, r: dict, priors: dict[str, PriorState]) -> float:
    comp_w = _posterior_mean_weight_v4("competition_weight", priors)
    comp_k = _posterior_mean_decay_v4("competition_decay_rate", priors)

    if dim == "competition_weight":
        top_rev = max(int(r.get("top_reviews") or 0), 1)
        return comp_w * math.exp(-comp_k * top_rev)
    elif dim == "commercial_value_weight":
        return _commercial_value(r)[0]
    elif dim == "long_tail_potential_weight":
        return _long_tail_potential(r)[0]
    # ... 其余维度同构
```

### 3.2 协方差矩阵替代独立假设

对 competition + dispersion 做联合更新，引入 2x2 协方差矩阵：

```python
def _estimate_cov_comp_disp(rows: list[dict], priors: dict) -> float:
    """从批次数据中估计 competition 和 dispersion 维度的协方差。"""
    comp_vals, disp_vals = [], []
    for r in rows:
        c = _dimension_contribution_v4("competition_weight", r, priors)
        d = _dimension_contribution_v4("dispersion_weight", r, priors)
        comp_vals.append(c)
        disp_vals.append(d)
    # ... 计算协方差
```

可信区间修正：`var_total = var_comp + var_disp + 2 * cov_comp_disp + ...`

### 3.3 新维度先验初始化

```python
for dim, (weight, max_val) in {
    "commercial_value_weight": (20.0, 20.0),
    "long_tail_potential_weight": (15.0, 15.0),
}.items():
    p = weight / max_val
    alpha = max(p * _PRIOR_EFFECTIVE_N, 0.5)
    beta_param = max((1 - p) * _PRIOR_EFFECTIVE_N, 0.5)
    priors[dim] = PriorState(dimension=dim, alpha=alpha, beta_param=beta_param,
                             mu=weight, sigma_sq=0.0, n_obs=0)
```

### 3.4 `_estimate_decay_rates` 修正

用 `_dimension_contribution_v4` 计算精确的 competition 维度贡献值，替代总分近似。

---

## 4. evolution.py v2 方案

### 4.1 top_n 扩展到 30

```python
def generate_new_seeds(batch_id: str, top_n: int = 30) -> tuple[list[str], str]:
```

取 30 条上下文但只生成 20 条新种子。

### 4.2 Jaccard threshold 从 0.6 调到 0.4

```python
def _is_too_similar(new_seed: str, existing_seeds: list[str], threshold: float = 0.4) -> bool:
```

效果：
- `track pet vaccination` vs `track dog vaccination` → Jaccard 0.5 > 0.4 → 去重（正确）
- `track freelance income` vs `track freelance tax deduction` → Jaccard 0.4 → 保留（正确）

### 4.3 剪枝阈值重设

```python
_WEAK_AVG_SCORE_V4 = 55.0  # 对应 v2 的 40，按比例 40 * (200/144) ≈ 55
_WEAK_MIN_KEYWORDS = 3     # 保持不变
```

版本感知剪枝：根据 `_SCORER_VERSION` 选择阈值。

### 4.4 种子生成 prompt v2

- 分层输入（高分/中分/低分高覆盖/趋势/外部信号）
- 每行增加 `volume_tier` 和 `trends_slope` 字段
- 新增趋势洞察摘要
- 强调痛点场景词优先

---

## 5. config_data.py 种子分类重构

### 5.1 新数据结构

```python
class SeedEntry(TypedDict):
    seed: str
    category: str       # "pain_point" | "category_word" | "trend_word"
    confidence: float   # 0.0-1.0

SEEDS_V2: list[SeedEntry] = [
    {"seed": "split rent with roommate", "category": "pain_point", "confidence": 0.95},
    {"seed": "track freelance income tax", "category": "pain_point", "confidence": 0.90},
    # ...
]

SEEDS: list[str] = [e["seed"] for e in SEEDS_V2]  # 向后兼容
```

### 5.2 分类规则

- 含动词 + 具体场景词 → `pain_point`
- 仅含品类名 + 修饰词 → `category_word`
- 含时效性特征（ai, crypto, carbon, remote） → `trend_word`

### 5.3 Bootstrap 种子替换

| 旧种子（删除） | 新种子（替换） | category |
|----------------|---------------|----------|
| `calculate medication schedule` | `remind pill when to take` | pain_point |
| `convert currency travel` | `convert currency without internet` | pain_point |
| `remind lease` | `track lease renewal date` | pain_point |
| `convert sleep` | `improve sleep quality naturally` | pain_point |
| `flashcard study app` | `study flashcards for medical exam` | pain_point |
| `schedule study plan` | `create study schedule for finals` | pain_point |

---

## 6. database.py Schema 迁移方案

### 6.1 aso_seeds 增加 category 列

```sql
ALTER TABLE `aso_seeds`
  ADD COLUMN `category`
    ENUM('pain_point', 'category_word', 'trend_word')
    NOT NULL DEFAULT 'pain_point'
  AFTER `source`;
```

### 6.2 aso_keywords 新增 6 列

```sql
ALTER TABLE `aso_keywords` ADD COLUMN `search_volume_tier` TINYINT DEFAULT 0;
ALTER TABLE `aso_keywords` ADD COLUMN `trends_avg_interest` FLOAT DEFAULT 0;
ALTER TABLE `aso_keywords` ADD COLUMN `trends_slope` FLOAT DEFAULT 0;
ALTER TABLE `aso_keywords` ADD COLUMN `trends_slope_latest` FLOAT DEFAULT 0;
ALTER TABLE `aso_keywords` ADD COLUMN `commercial_value_score` INT DEFAULT 0;
ALTER TABLE `aso_keywords` ADD COLUMN `long_tail_score` INT DEFAULT 0;
```

### 6.3 aso_score_priors 新维度记录

```sql
INSERT IGNORE INTO aso_score_priors (dimension, alpha, beta_param, mu, sigma_sq, n_obs, updated_at)
VALUES
  ('commercial_value_weight', 20.0, 0.01, 20.0, 0.0, 0, NOW()),
  ('long_tail_potential_weight', 15.0, 0.01, 15.0, 0.0, 0, NOW()),
  ('cov_competition_dispersion', 0.0, 0.0, 0.0, 0.0, 0, NOW());
```

### 6.4 数据回填策略

- `aso_seeds.category`：启发式回填（含动词→pain_point，仅品类名→category_word）
- `aso_keywords` 新列：默认值 0，下次扫描自然填充
- `aso_score_priors`：`get_current_priors()` 自动补齐缺失维度

### 6.5 完整迁移脚本

所有 ALTER 使用 `information_schema` 检查列是否存在，确保幂等执行。

---

## 7. 接口兼容性分析

### 7.1 API 响应字段变更

**GET /analysis/top, /analysis/compare, /seeds/{seed}/keywords** -- 新增 6 个字段（向后兼容）：
- `search_volume_tier`, `trends_avg_interest`, `trends_slope`, `trends_slope_latest`
- `commercial_value_score`, `long_tail_score`

**GET /seeds/list** -- 新增 `category` 字段

**GET /analysis/priors** -- 新增 3 个维度条目

### 7.2 前端适配点

- **dashboard.html**：新增搜索量级/商业价值/长尾潜力列，v4 标签阈值 100/70/40
- **seeds-dashboard.html**：新增分类列，按 category 分组显示
- **keyword-insights.html**：新增统计卡片，报告 prompt 包含新维度

### 7.3 向后兼容策略

| 策略 | 实现方式 |
|------|---------|
| 旧字段不变 | `blue_ocean_score`, `blue_ocean_label`, `blue_ocean_flags` 含义和类型不变 |
| 新字段默认值 | 所有新增列 DEFAULT 0，旧数据查询自然返回零值 |
| 评分版本切换 | `ASO_SCORER_VERSION` 环境变量控制，默认 4 |
| 旧函数保留 | v2/v3 函数不删除不修改 |
| 标签阈值版本感知 | `blue_ocean_label()` 根据 `_SCORER_VERSION` 选择阈值 |
| 前端降级 | 新字段做 `\|\| 0` 兜底 |
| DB 迁移幂等 | ALTER 前检查列是否存在 |
| API 响应扩展 | 只增不删 |

---

## 关键实施文件（按优先级）

1. `aso_core/scorer.py` -- 新增 v4 评分函数和维度
2. `app/bayesian_updater.py` -- 重构维度贡献计算和协方差
3. `aso_core/trends.py` -- 新增 interest over time 和搜索量级
4. `app/evolution.py` -- top_n/Jaccard/剪枝/prompt 升级
5. `app/database.py` -- Schema 迁移和字段扩展
