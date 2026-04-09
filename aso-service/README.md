# ASO 蓝海关键词分析服务

将本地 ASO 扫描脚本服务化：FastAPI 提供 HTTP 接口、MySQL 8.0 持久化、API 密钥鉴权，支持 Docker 一键部署与 n8n 定时调用。

核心业务逻辑在仓库根目录的 **`aso_core`** 包中；本目录仅保留 HTTP 与数据库层。

## 部署命令

构建上下文为**仓库根目录**（以便打包 `aso_core` 与 `app`）。请在 **`aso-service`** 目录下执行：

```bash
cd aso-service
cp .env.example .env
# 编辑 .env 填入密码和 API_KEY
docker compose up -d --build

# 查看启动日志
docker compose logs -f aso-service
```

依赖统一维护在仓库根目录 [`requirements.txt`](../requirements.txt)，镜像构建时从根目录复制该文件。

## 手动触发一次扫描（测试用）

```bash
curl -X POST http://localhost:8000/scan/start \
  -H "X-API-Key: 你的API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"country": "us"}'
```

## 查询任务状态

```bash
curl http://localhost:8000/scan/status/{batch_id} \
  -H "X-API-Key: 你的API_KEY"
```

## 拉取分析结果

```bash
curl "http://localhost:8000/analysis/top?label=💎%20金矿&limit=20&days=7" \
  -H "X-API-Key: 你的API_KEY"
```

说明：`label` 含空格或 emoji 时请对 URL 进行编码（如上例中的 `%20`）。

## 接口说明

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/health` | 否 | 健康检查 |
| POST | `/scan/start` | 是 | 异步启动全量扫描，Body 可选 `{"country":"us"}` |
| GET | `/scan/status/{batch_id}` | 是 | 查询任务状态 |
| GET | `/analysis/top` | 是 | Query：`label`、`limit`（默认 50，最大 200）、`days`（默认 7） |

`rank_history.json` 通过环境变量 `RANK_HISTORY_PATH` 指定（默认 `/data/rank_history.json`），在 Compose 中挂载到 `aso-data` 卷，容器重启后不丢失。

## n8n Workflow 1：定时扫描（建议每周一 02:00）

**节点1 — Schedule Trigger**

- 每周一 02:00 触发

**节点2 — HTTP Request（触发扫描）**

- Method: POST
- URL: `http://aso-service:8000/scan/start`
- Headers: `X-API-Key` → `{{$env.ASO_API_KEY}}`
- Body (JSON): `{"country": "us"}`

**节点3 — Wait**

- 等待 90 分钟（扫描预留时间）

**节点4 — HTTP Request（确认完成）**

- Method: GET
- URL: `http://aso-service:8000/scan/status/{{$node["触发扫描"].json.batch_id}}`
- Headers: `X-API-Key` → `{{$env.ASO_API_KEY}}`

## n8n Workflow 2：分析推送（建议每周二 09:00）

**节点1 — Schedule Trigger**

- 每周二 09:00 触发

**节点2 — HTTP Request（拉取蓝海数据）**

- Method: GET
- URL: `http://aso-service:8000/analysis/top?label=💎 金矿&limit=20&days=7`
- Headers: `X-API-Key` → `{{$env.ASO_API_KEY}}`

**节点3 — AI 模型节点（Claude / GPT）**

System Prompt：

```
你是一位 App Store 市场分析师，擅长从关键词数据中识别产品机会。
请用中文回答，结构清晰，重点突出。
```

User Prompt 模板：

```
以下是本周 App Store 蓝海关键词扫描结果，请完成以下分析：

1. 推荐最值得立项的3个关键词，说明理由
2. 识别这批词背后共同的用户需求模式
3. 对每个推荐词给出 MVP 核心功能（1句话）
4. 指出需要警惕的风险点

数据如下：
{{$json.keywords}}
```

**节点4 — 飞书 Webhook**

- Method: POST
- URL: 飞书机器人 Webhook 地址
- Body:

```json
{
  "msg_type": "text",
  "content": {
    "text": "📊 本周 ASO 蓝海分析报告\n\n{{$json.choices[0].message.content}}"
  }
}
```

## 本地开发（非 Docker）

在 **`aso-service`** 目录启动，将**仓库根目录**加入 `PYTHONPATH` 以加载 `aso_core`：

```bash
cd /path/to/aso_opportunities/aso-service
python -m venv .venv
source .venv/bin/activate
pip install -r ../requirements.txt
export PYTHONPATH=..
export $(grep -v '^#' .env | xargs)  # 或手动 export MySQL 等变量
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

需本地已安装 MySQL 8.0，并在 `.env` 中将 `MYSQL_HOST` 设为 `127.0.0.1`。

## 技术约束

- 后台任务使用 `threading.Thread(daemon=True)`，无 Celery / Redis
- 数据库访问使用 `pymysql`，无 ORM
- 采集与评分逻辑与仓库根目录脚本保持一致（种子词、请求间隔、评分规则未改）
