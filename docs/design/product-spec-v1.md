# ASO 核心算法升级 - 产品规格书 v1

---

## 文档信息

| 字段 | 值 |
|------|-----|
| 版本 | v1.0 |
| 日期 | 2026-04-26 |
| 状态 | 待架构师评审 |

---

## 1. 种子分类体系定义

### 1.1 三级分类模型

| 分类 | 定义 | 判定规则 | 示例 | 优先级权重 |
|------|------|----------|------|-----------|
| **L1: 痛点场景词** | 用户在具体困境下的搜索表达 | 包含「动作+对象+情境」三要素中至少两要素；词长 3-6 个单词；不含纯品类名 | `split rent with roommate`, `track freelance income for tax` | 1.0（最高） |
| **L2: 品类词** | 应用品类/功能类别名称 | 2 个单词以内；或「品类名 + 修饰词」结构；Autocomplete 排名靠前（<=5） | `budget tracker`, `medication reminder` | 0.6 |
| **L3: 趋势词** | 从外部信号源捕获的新兴搜索需求 | 来自 Google Trends Rising/Related Queries；过去 90 天内搜索量上升 >=50%；与现有种子 Jaccard < 0.4 | `ai meal planner`, `crypto tax calculator 2025` | 0.8 |

### 1.2 判定规则详细定义

**痛点场景词（L1）判定流水线：**

```
输入: seed 字符串
  │
  ├─> 词数检查: 3 <= word_count <= 6 ? 继续 : 降级到 L2
  ├─> 品类名黑名单检查: 不在 BLACKLIST_CATEGORY_WORDS 中 ? 继续 : 降级到 L2
  ├─> 意图要素检查: 包含 (动作动词 OR 痛点形容词) AND (对象名词) ? 继续 : 降级到 L2
  ├─> 特异性检查: 与通用词表（top 1000 English words）重叠率 < 70% ? 继续 : 降级到 L2
  └─> 输出: L1
```

- `BLACKLIST_CATEGORY_WORDS` = `["app", "tracker", "calculator", "reminder", "planner", "manager", "helper", "tool"]`

**品类词（L2）判定流水线：**

```
输入: seed 字符串
  │
  ├─> 词数检查: word_count <= 3 ? 继续 : 非 L2
  ├─> Autocomplete 排名检查: 该词在 Apple Autocomplete 中排名 <= 5 ? 继续 : 非 L2
  └─> 输出: L2
```

**趋势词（L3）判定流水线：**

```
输入: 外部趋势词候选
  │
  ├─> 时效性检查: 趋势上升发生在过去 90 天内 ? 继续 : 丢弃
  ├─> 与现有种子去重: Jaccard(候选, 所有现有种子) < 0.4 ? 继续 : 丢弃
  ├─> App Store 相关性校验: 用该词查 Apple Autocomplete 至少返回 1 条结果 ? 继续 : 丢弃
  ├─> 落地转换: 将趋势词转化为 App Store 搜索意图表达
  └─> 输出: L3
```

### 1.3 迁移策略

| 阶段 | 动作 | 触发条件 |
|------|------|----------|
| **Phase 1: 标记** | 对现有种子运行分类器，标记 L1/L2/L3 类别 | 部署时一次性执行 |
| **Phase 2: 剪枝** | L2 品类词中 avg_score < 35 且 strong_count = 0 的标记 pruned；L1 词放宽到 avg_score < 30 | 首次全量扫描后 |
| **Phase 3: 填充** | 启动 Google Trends 种子发现流程，补充 L1/L3 种子，目标比例 L1:L2:L3 = 6:2:2 | 持续进行 |

---

## 2. Google Trends 种子发现流程

### 2.1 完整 Pipeline

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Google Trends  │     │   算法筛选引擎    │     │  种子落地转换器  │
│   数据源采集     │ ──> │  (ASO价值判断)   │ ──> │ (App Store意图) │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
  • Rising Queries         • 搜索量级分级         • 痛点场景化改写
  • Related Queries        • 竞争空白检测         • Autocomplete 校验
  • Search Volume          • 长尾性评分           • 去重入库
```

### 2.2 数据源定义

| 数据源 | API/方法 | 采集频率 | 输出 |
|--------|----------|----------|------|
| **Rising Queries** | `pytrends.related_queries()` rising | 每周 | 上升查询词列表 + 上升百分比 |
| **Related Queries** | `pytrends.related_queries()` top | 每周 | 相关查询词列表 + 搜索量指数 |
| **Search Volume** | `pytrends.interest_over_time()` | 每月 | 时间序列搜索量数据 |

### 2.3 算法筛选规则

```
输入: Google Trends 候选词
  │
  ├─> [Step 1] 搜索量级分级
  │     指数 >= 50: 高搜索量 → 需重点评估竞争空白
  │     指数 20-49: 中搜索量 → 优先候选
  │     指数 < 20: 低搜索量 → 仅当竞争极弱时保留
  │
  ├─> [Step 2] 竞争空白检测
  │     用候选词查 Apple Autocomplete
  │     结果数 <= 3: 强候选
  │     结果数 4-10: 中候选
  │     结果数 > 10: 弱候选
  │
  ├─> [Step 3] 长尾性初筛
  │     词长度 >= 3 个单词
  │     不含高频通用词占比 > 50%
  │
  ├─> [Step 4] 与现有种子去重
  │     Jaccard(候选, 所有 active 种子) < 0.35
  │
  └─> 输出: 通过筛选的候选词列表
```

### 2.4 种子落地策略

| 趋势词类型 | 转换策略 | 示例 |
|-----------|----------|------|
| 纯品类趋势词 | 添加痛点场景修饰 | `ai meal planner` → `plan weekly meals with ai suggestions` |
| 动作趋势词 | 保持动作，添加具体对象/情境 | `track crypto` → `track crypto portfolio for taxes` |
| 问题型趋势词 | 直接作为种子 | `how to split rent fairly` → `split rent fairly with roommates` |
| 品牌/产品趋势词 | 丢弃 | `chatgpt app` → 丢弃 |

---

## 3. 商业价值评分维度

新增 3 个商业价值子维度，总权重 0-25 分：

### 3.1 搜索量级维度 (0-10)

| 分级 | 判定标准 | 得分 |
|------|----------|------|
| 高搜索量 | Google Trends 指数 >= 50 或 Autocomplete rank <= 3 | 10 |
| 中搜索量 | 指数 20-49 或 rank 4-8 | 6 |
| 低搜索量 | 指数 < 20 或 rank > 8 | 3 |
| 无信号 | 无法获取数据且 rank > 15 | 1 |

```python
def search_volume_score(record: dict) -> float:
    rank = record.get("autocomplete_rank", 99)
    trends_index = record.get("gtrends_search_index", 0)
    rank_score = max(0, 10 - rank)
    trends_score = min(10, trends_index / 10) if trends_index else 0
    if trends_index:
        return round(trends_score * 0.7 + rank_score * 0.3, 1)
    return round(rank_score, 1)
```

### 3.2 付费意愿维度 (0-8)

| 信号 | 量化方式 | 得分 |
|------|----------|------|
| 竞品定价区间 | 头部 3 个应用平均价格 >= $4.99 | 3 |
| 订阅模式占比 | 头部 5 个中 >= 3 个含 "subscription"/"premium" | 3 |
| 高价值品类 | 命中 finance/business/health 等品类 | 2 |

高价值品类词表：
- finance: budget, invest, tax, invoice, expense, accounting
- business: freelance, small business, contractor, mileage, reimbursement
- health: medication, therapy, chronic, symptom, diagnosis
- education: certification, exam prep, language learning, tutor
- productivity: automation, workflow, crm, project management

### 3.3 市场规模维度 (0-7)

| 信号 | 量化方式 | 得分 |
|------|----------|------|
| 竞品总评论数 | >= 100000 = 3分, >= 10000 = 2分, >= 1000 = 1分 | 0-3 |
| 品类深度 | 应用数 >= 50 = 2分, >= 20 = 1分 | 0-2 |
| 跨平台验证 | 双平台均有结果 | 2 |

---

## 4. 长尾性评分维度

新增 3 个长尾性子维度，总权重 0-15 分：

### 4.1 关键词长度维度 (0-5)

| 词长（单词数） | 得分 |
|---------------|------|
| >= 5 | 5 |
| 4 | 4 |
| 3 | 2 |
| 2 | 0.5 |
| 1 | 0 |

### 4.2 特异性维度 (0-5)

- 通用词重叠率 < 30% = 5分；30-50% = 3分；> 50% = 1分
- 含专有名词 +1，含数字/年份 +0.5

### 4.3 搜索意图深度维度 (0-5)

| 意图类型 | 判定规则 | 得分 |
|----------|----------|------|
| 交易型 | 含 app, download, best, top rated, for iphone | 5 |
| 导航型 | 含品类词（tracker, calculator, planner）+ 修饰词 | 3 |
| 信息型 | 含 how to, what is, why, tips | 1 |

---

## 5. 种子进化策略 v2

### 5.1 视野扩展：分层采样

| 层级 | 数量 | 选取标准 | 目的 |
|------|------|----------|------|
| 高分层 | 15 | score >= 55 | 确认有效模式 |
| 中分层 | 15 | score 35-54，seed_coverage >= 3 | 发现多路径触发潜力词 |
| 低分高覆盖层 | 10 | score < 35，seed_coverage >= 5 | 发现被低估的搜索意图 |
| 趋势层 | 10 | trends_rising = True | 捕捉新兴需求 |
| 外部信号层 | 10 | 来自 Google Trends 相关查询 | 引入外部视野 |

**总输入量：60 条关键词（替代原来的 10 条）**

### 5.2 剪枝阈值重设

按种子类别区分：
- L1 痛点场景词: avg_score < 30
- L2 品类词: avg_score < 35
- L3 趋势词: avg_score < 25（3 个批次观察期）

额外条件：最近 3 个批次内无改善趋势 + 非手动创建

### 5.3 Jaccard 阈值调整

从 0.6 调到 **0.35**

效果示例：
- `split rent with roommate` vs `split bill with friends` → Jaccard 0.17 → 保留
- `track freelance income` vs `track freelance money` → Jaccard 0.5 → 去重
- `remind medication schedule` vs `remind pill vitamin` → Jaccard 0.25 → 保留

### 5.4 种子生成 Prompt v2

分层输入，包含 5 个层次的关键词数据，优先从中分潜力层和低分覆盖层发现新场景。

---

## 6. 标签阈值体系 v2

基于 v4 满分 ~172 重新校准：

| 标签 | v4 分数范围 | 占比目标 |
|------|------------|----------|
| 💎 金矿 | >= 90 | ~8% |
| 🟢 蓝海 | 65-89 | ~25% |
| 🟡 观察 | 40-64 | ~35% |
| 🔴 跳过 | < 40 | ~32% |

**长尾优势标记**：长尾性分数 >= 10 的关键词，即使总分在 🟡 区间，也给予 `🟡+ 长尾观察` 特殊标记。

**商业价值标记**：
- 商业价值 >= 20 → `💰 高商业`
- 商业价值 15-19 → `💰 中商业`

---

## 7. 关键词报告的商业价值分级标准

### 7.1 新报告结构

```markdown
# ASO 关键词洞察报告 v{version}

## 1. 本期核心信号
## 2. 高商业价值推荐（Top 5）
## 3. 长尾金矿词（隐藏机会）
## 4. 需求模式识别
## 5. 趋势预测
## 6. 本期变化对比
## 7. 风险提示
```

### 7.2 预测信号来源

| 信号 | 预测逻辑 | 置信度 |
|------|----------|--------|
| Google Trends 上升 | 过去 30 天持续上升 + 无放缓迹象 | 高 |
| 排名变化 | 最近 3 次扫描排名持续上升 | 中 |
| 新种子产出 | 新种子首次扫描即产出高分词 | 高 |
| 竞品更新停滞 | 头部竞品 > 12 个月未更新 + 新竞品进入 | 中 |
| 跨平台验证 | Google Play 出现相同需求但 App Store 竞争弱 | 高 |

---

## 8. 新增 API 字段

| 字段 | 表 | 类型 | 说明 |
|------|-----|------|------|
| `seed_category` | `aso_seeds` | ENUM | 种子三级分类 |
| `seed_quality_score` | `aso_seeds` | FLOAT | 种子综合质量分 |
| `trend_source` | `aso_seeds` | VARCHAR | 趋势词来源 |
| `blue_ocean_score_v4` | `aso_keywords` | INT | v4 评分 |
| `commercial_value_score` | `aso_keywords` | INT | 商业价值子分 |
| `search_volume_score` | `aso_keywords` | FLOAT | 搜索量级子分 |
| `monetization_score` | `aso_keywords` | FLOAT | 付费意愿子分 |
| `market_size_score` | `aso_keywords` | FLOAT | 市场规模子分 |
| `long_tail_score` | `aso_keywords` | FLOAT | 长尾性总分 |
| `gtrends_search_index` | `aso_keywords` | INT | Google Trends 搜索量指数 |
| `gtrends_trend_direction` | `aso_keywords` | ENUM | 趋势方向 |
| `avg_app_price` | `aso_keywords` | FLOAT | 竞品平均定价 |
| `subscription_ratio` | `aso_keywords` | FLOAT | 订阅模式占比 |

## 9. 新增模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 种子发现器 | `aso_core/seed_discovery.py` | Google Trends 种子发现 |
| 种子分类器 | `aso_core/seed_classifier.py` | 种子三级分类判定 |
| 商业价值评分 | `aso_core/commercial_scorer.py` | 商业价值和长尾性评分 |
| 阈值校准器 | `aso_core/threshold_calibrator.py` | 标签阈值自动校准 |
