# ASO Keyword Engine

> 通过 Apple Autocomplete API 挖掘 App Store 蓝海关键词，
> 结合竞争分析与 AI 种子进化，自动产出高价值选词报告。

---

**核心思路**

不从品类出发，从「行为动词 × 生活场景」的笛卡尔积出发，
构造用户真实会输入 App Store 搜索框的意图词作为种子。

通过 Apple 官方 Autocomplete 接口（按真实搜索频率排序）
和 iTunes Search API（获取竞品评论与更新数据），
计算每个关键词的蓝海机会分。

系统会自动分析哪些种子词产出率高，
通过 Claude API 推断新的种子词，逐代进化种子矩阵。

---

**核心业务流程**

```
[n8n 定时触发]
      ↓
POST /scan/start
      ↓
验证 pending 种子 → 从数据库读取 active 种子
      ↓
多国家遍历（us / gb / au / ca）
      ↓
Apple Autocomplete API → 关键词列表（含搜索量排名）
iTunes Search API      → 竞争数据（评论数、更新时间、集中度）
      ↓
蓝海评分（搜索量真实性 + 竞争强度 + 趋势信号）
      ↓
写入 MySQL → 触发种子进化
      ↓
Claude API 分析高效种子 → 生成新种子（pending）
      ↓
[下周全量扫描前验证激活]

[n8n 分析触发]
      ↓
GET /analysis/compare（窗口函数对比趋势）
      ↓
n8n AI 节点（Claude/GPT）生成报告
      ↓
推送飞书
```

---

**API 接口说明**

所有接口（除 /health）需在 Header 携带：`X-API-Key: 你的API_KEY`

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | /scan/start | 触发扫描（mode: full/tracking） |
| GET  | /scan/status/{batch_id} | 查询任务进度 |
| GET  | /analysis/top | 拉取蓝海词列表 |
| GET  | /analysis/compare | 拉取趋势对比数据 |
| GET  | /seeds/status | 查看种子矩阵进化状态 |
| POST | /report/generate | 手动触发生成洞察报告 |
| GET  | /report/check | 检查是否应触发报告 |
| GET  | /report/latest | 获取最新报告全文 |
| GET  | /report/history | 获取历史报告列表 |
| GET  | /report/{id} | 获取指定报告全文 |
| GET  | /health | 健康检查（无需鉴权） |

---

**Query 参数说明**

/analysis/top
- `label`: 💎 金矿 / 🟢 蓝海 / 🟡 观察 / 🔴 跳过
- `limit`: 默认 50，最大 200
- `days`: 默认 7
- `countries`: 逗号分隔，如 `us,gb`

/analysis/compare
- `days_recent`: 默认 7
- `days_baseline`: 默认 14

---

**部署方法**

```bash
git clone https://github.com/yourname/aso-keyword-engine.git
cd aso-keyword-engine

cp .env.example .env
# 编辑 .env，填写 API_KEY / ANTHROPIC_API_KEY / MySQL 密码

docker compose up -d --build

# 验证启动
curl http://localhost:8000/health

# 触发首次全量扫描
curl -X POST http://localhost:8000/scan/start \
  -H "X-API-Key: 你的API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode": "full"}'
```

### 种子进化状态页面

服务启动后，访问以下地址查看种子矩阵的实时进化状态：

```
http://your-server:8000/static/seeds-dashboard.html
```

输入 API Key 后即可查看：
- 各状态种子数量统计
- 待激活种子列表
- 最近进化事件时间线

### 关键词洞察报告页面

```
http://your-server:8000/static/keyword-insights.html
```

AI 自动在以下任一条件满足时生成新报告：
- 新增 💎 金矿词 >= REPORT_MIN_NEW_GOLD 个
- 近7天 score 总变化量 >= REPORT_MIN_SCORE_DELTA
- 关键词总数变化 >= REPORT_MIN_KEYWORD_CHANGE 个
- 每次周全量扫描结束后强制生成

每次生成报告时，AI 会将上一份报告作为记忆注入，
实现分析结论的持续迭代与自我修正。
历史报告全部持久化，可在页面内随时查阅。

手动触发：
```bash
curl -X POST http://localhost:8000/report/generate \
  -H "X-API-Key: 你的API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"force": true}'
```

---

**n8n 接入**

1. 在 n8n 环境变量中设置 `ASO_API_KEY`
2. 导入 `docs/n8n-workflows/` 目录下的两个 workflow JSON
3. 修改 HTTP Request 节点的 URL 为你的服务地址
4. 修改飞书节点的 Webhook 地址
5. 激活两个 workflow

---

**蓝海评分说明**

| 标签 | 分数 | 含义 |
|------|------|------|
| 💎 金矿 | 80–110 | 立即立项 |
| 🟢 蓝海 | 60–79  | 重点调研 |
| 🟡 观察 | 40–59  | 积累趋势 |
| 🔴 跳过 | 0–39   | 竞争过激或需求弱 |

评分维度：
- **搜索量真实性**（30 分）：同一词被多条种子路径触发
- **竞争强度**（50 分）：头部评论数、市场集中度、竞品更新活跃度
- **趋势信号**（30 分）：跨国家梯度差、历史排名变化

---

**数据成熟时间线**

- **第 1 周**：静态快照，查看当前蓝海词分布
- **第 2 周**：rank_change 生效，排名趋势可见
- **第 4 周**：持续在榜词与昙花一现词开始分野
- **第 8 周**：种子矩阵完成首轮进化，分析结论可信度高

---

**项目结构**

```
├── .env.example          # 环境变量模板
├── Dockerfile            # 容器构建
├── docker-compose.yml    # 编排（aso-service + MySQL）
├── requirements.txt      # Python 依赖
├── main.py               # CLI 入口（本地单次扫描）
├── aso_core/             # 纯采集 / 评分引擎
│   ├── autocomplete.py   # Apple Autocomplete API
│   ├── competition.py    # iTunes Search API + 竞争分析
│   ├── scanner.py        # 多国家扫描编排
│   ├── scorer.py         # 蓝海评分算法
│   ├── config_data.py    # 种子词矩阵（动词 × 场景）
│   └── settings.py       # 配置管理
├── app/                  # FastAPI 服务层
│   ├── main.py           # 入口 + startup + /health
│   ├── auth.py           # X-API-Key 鉴权
│   ├── database.py       # MySQL 连接 + SQL 函数
│   ├── evolution.py      # 种子进化引擎
│   ├── report_engine.py  # 关键词洞察报告引擎
│   └── routers/
│       ├── scan.py       # /scan/start, /scan/status
│       ├── analysis.py   # /analysis/top, /analysis/compare
│       ├── seeds.py      # /seeds/status
│       └── report.py     # /report/* 报告接口
└── docs/
    ├── architecture.md   # 架构说明 + Mermaid 流程图
    ├── cursor-prompts.md # Claude 二次筛选 Prompt 模板
    └── n8n-workflows/    # n8n 工作流导入说明
```

---

**技术栈**

- Python 3.11 / FastAPI / Uvicorn
- MySQL 8.0（pymysql，手写 SQL，无 ORM）
- Docker Compose
- Anthropic Claude API（种子进化）
- n8n（外部编排 + AI 报告 + 飞书推送）

---

**License**

见 [LICENSE](LICENSE) 文件。

---
---

# ASO Keyword Engine (English)

> Mine blue-ocean keywords from the App Store via Apple Autocomplete API,
> combined with competition analysis and AI-driven seed evolution to automatically
> produce high-value keyword selection reports.

---

**Core Idea**

Instead of starting from app categories, we start from a Cartesian product of
"action verbs × life scenarios" to construct intent-driven seed phrases that
real users would type into the App Store search box.

Using Apple's official Autocomplete API (ranked by actual search frequency)
and the iTunes Search API (competitor reviews and update data),
we calculate a blue-ocean opportunity score for each keyword.

The system automatically analyzes which seed phrases have the highest yield,
and uses the Claude API to infer new seeds, evolving the seed matrix generation
by generation.

---

**Core Workflow**

```
[n8n Scheduled Trigger]
      ↓
POST /scan/start
      ↓
Validate pending seeds → Read active seeds from DB
      ↓
Multi-country iteration (us / gb / au / ca)
      ↓
Apple Autocomplete API → keyword list (with search volume ranking)
iTunes Search API      → competition data (reviews, update age, concentration)
      ↓
Blue-ocean scoring (search authenticity + competition intensity + trend signals)
      ↓
Write to MySQL → Trigger seed evolution
      ↓
Claude API analyzes top seeds → Generate new seeds (pending)
      ↓
[Validated and activated before next weekly full scan]

[n8n Analysis Trigger]
      ↓
GET /analysis/compare (window function trend comparison)
      ↓
n8n AI Node (Claude/GPT) generates report
      ↓
Push to Feishu (Lark)
```

---

**API Reference**

All endpoints (except /health) require the header: `X-API-Key: YOUR_API_KEY`

| Method | Path | Purpose |
|--------|------|---------|
| POST | /scan/start | Trigger scan (mode: full/tracking) |
| GET  | /scan/status/{batch_id} | Query task progress |
| GET  | /analysis/top | Fetch blue-ocean keyword list |
| GET  | /analysis/compare | Fetch trend comparison data |
| GET  | /seeds/status | View seed matrix evolution status |
| POST | /report/generate | Manually trigger insight report |
| GET  | /report/check | Check if report should be triggered |
| GET  | /report/latest | Get latest report full text |
| GET  | /report/history | Get historical report list |
| GET  | /report/{id} | Get specific report full text |
| GET  | /health | Health check (no auth required) |

---

**Query Parameters**

/analysis/top
- `label`: 💎 Gold Mine / 🟢 Blue Ocean / 🟡 Watch / 🔴 Skip
- `limit`: default 50, max 200
- `days`: default 7
- `countries`: comma-separated, e.g. `us,gb`

/analysis/compare
- `days_recent`: default 7
- `days_baseline`: default 14

---

**Deployment**

```bash
git clone https://github.com/yourname/aso-keyword-engine.git
cd aso-keyword-engine

cp .env.example .env
# Edit .env: fill in API_KEY / ANTHROPIC_API_KEY / MySQL passwords

docker compose up -d --build

# Verify startup
curl http://localhost:8000/health

# Trigger first full scan
curl -X POST http://localhost:8000/scan/start \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode": "full"}'
```

### Seed Evolution Status Page

After starting the service, visit the following URL to view real-time seed matrix evolution status:

```
http://your-server:8000/static/seeds-dashboard.html
```

Enter your API Key to view:
- Seed count statistics by status
- Pending seeds list
- Recent evolution event timeline

### Keyword Insights Report Page

```
http://your-server:8000/static/keyword-insights.html
```

AI automatically generates a new report when any of the following conditions are met:
- New 💎 Gold Mine keywords >= REPORT_MIN_NEW_GOLD
- Total score change in last 7 days >= REPORT_MIN_SCORE_DELTA
- Total keyword count change >= REPORT_MIN_KEYWORD_CHANGE
- After every weekly full scan

Each report generation injects the previous report as memory context,
enabling continuous iteration and self-correction of analysis conclusions.
All historical reports are persisted and can be viewed on the page at any time.

Manual trigger:
```bash
curl -X POST http://localhost:8000/report/generate \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"force": true}'
```

---

**n8n Integration**

1. Set `ASO_API_KEY` in your n8n environment variables
2. Import the two workflow JSON files from `docs/n8n-workflows/`
3. Update the HTTP Request node URL to your service address
4. Update the Feishu (Lark) node Webhook URL
5. Activate both workflows

---

**Blue-Ocean Scoring**

| Label | Score | Meaning |
|-------|-------|---------|
| 💎 Gold Mine | 80–110 | Greenlight immediately |
| 🟢 Blue Ocean | 60–79 | Priority research |
| 🟡 Watch | 40–59 | Accumulate trend data |
| 🔴 Skip | 0–39 | Over-competitive or weak demand |

Scoring dimensions:
- **Search Authenticity** (30 pts): Same keyword triggered by multiple seed paths
- **Competition Intensity** (50 pts): Top app reviews, market concentration, competitor update frequency
- **Trend Signals** (30 pts): Cross-country gradient, historical rank changes

---

**Data Maturity Timeline**

- **Week 1**: Static snapshot — view current blue-ocean keyword distribution
- **Week 2**: rank_change takes effect — ranking trends become visible
- **Week 4**: Persistent keywords vs. flash-in-the-pan keywords start to diverge
- **Week 8**: Seed matrix completes first evolution cycle — analysis conclusions become reliable

---

**Project Structure**

```
├── .env.example          # Environment variable template
├── Dockerfile            # Container build
├── docker-compose.yml    # Orchestration (aso-service + MySQL)
├── requirements.txt      # Python dependencies
├── main.py               # CLI entry (local one-off scan)
├── aso_core/             # Pure collection / scoring engine
│   ├── autocomplete.py   # Apple Autocomplete API
│   ├── competition.py    # iTunes Search API + competition analysis
│   ├── scanner.py        # Multi-country scan orchestration
│   ├── scorer.py         # Blue-ocean scoring algorithm
│   ├── config_data.py    # Seed matrix (verbs × scenarios)
│   └── settings.py       # Configuration management
├── app/                  # FastAPI service layer
│   ├── main.py           # Entry + startup + /health
│   ├── auth.py           # X-API-Key authentication
│   ├── database.py       # MySQL connection + SQL functions
│   ├── evolution.py      # Seed evolution engine
│   ├── report_engine.py  # Keyword insights report engine
│   └── routers/
│       ├── scan.py       # /scan/start, /scan/status
│       ├── analysis.py   # /analysis/top, /analysis/compare
│       ├── seeds.py      # /seeds/status
│       └── report.py     # /report/* report endpoints
└── docs/
    ├── architecture.md   # Architecture + Mermaid diagrams
    ├── cursor-prompts.md # Claude screening prompt template
    └── n8n-workflows/    # n8n workflow import guide
```

---

**Tech Stack**

- Python 3.11 / FastAPI / Uvicorn
- MySQL 8.0 (pymysql, hand-written SQL, no ORM)
- Docker Compose
- Anthropic Claude API (seed evolution)
- n8n (external orchestration + AI reports + Feishu push)

---

**License**

See [LICENSE](LICENSE).
