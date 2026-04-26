# 架构规格书：智能体认证方式适配 (auth_type)

> 文档版本: 1.0.0
> 日期: 2026-04-26
> 状态: 已确认

---

## 1. 基准探查

### 1.1 技术栈审计

| 层级 | 现有技术 | 备注 |
|------|---------|------|
| Web 框架 | FastAPI 0.111.0 + Uvicorn 0.29.0 | 路径参数、Pydantic Body 验证 |
| 数据库 | MySQL 8.0 + PyMySQL 1.1.0 | 无连接池，每查询开闭连接 |
| 加密 | Fernet (cryptography>=42) | 32字节 hex key，AES 对称加密 |
| HTTP 客户端 | requests>=2.31.0 | 无异步，同步调用 |
| 认证 | JWT (PyJWT 2.8.0) + bcrypt 4.1.3 + HMAC API Key | 双轨鉴权 |
| 前端 | Vanilla JS 单页 HTML | 无构建工具，原生 fetch |
| Schema 迁移 | `_add_column_if_not_exists` + 白名单 | 纯 SQL ALTER，无 Alembic |

### 1.2 现有代码热点

**`app/agent_client.py` (第 49-53 行) -- 核心改造目标:**

```python
headers = {
    "Content-Type": "application/json",
    "x-api-key": api_key,         # 现有：仅 Anthropic 原生认证
    "anthropic-version": version,
}
```

当前硬编码 `x-api-key` 头部。bearer 模式需要将此改为 `Authorization: Bearer {api_key}`，并移除 `anthropic-version` 头部。

**`app/database.py` -- Schema 迁移机制:**

白名单 `_ALLOWED_TABLES` 包含 `"aso_agents"`，但 `_ALLOWED_COLUMNS` 不包含 `"auth_type"`。新增字段必须先注册到白名单，然后通过 `_add_column_if_not_exists` 在 `init_db()` 中执行 ALTER。

**`app/routers/agents.py` -- API 响应序列化:**

`_agent_to_dict` 未包含 `auth_type`，需扩展。

**`app/static/agents.html` -- 前端表单:**

模态框仅有 name、base_url、api_key、model、version 五个表单字段，缺少 auth_type 下拉选择。

### 1.3 调用链路追踪

```
evolution.py::generate_new_seeds()
    -> call_agent("seed_evolution", prompt)
        -> get_assignment("seed_evolution")  -- JOIN aso_agent_assignments + aso_agents
        -> decrypt_api_key(agent["api_key_enc"])
        -> requests.post(endpoint, headers={...})

report_engine.py::run_report_generation()
    -> call_agent("keyword_report", prompt)
        -> [same path as above]
```

两个调用者仅传入 `usage` 和 `prompt`，不传入任何认证方式信息。认证方式必须从 DB agent 配置中自动读取。

### 1.4 现有 SSRF 防护

```python
_PRIVATE_HOST_RE = re.compile(r"^(127\.|10\.|...|localhost|::1|fe80:)", re.IGNORECASE)
# 强制 https 协议校验
if parsed.scheme != "https":
    raise ValueError(...)
```

此 HTTPS 强制校验会阻止 HTTP 协议的兼容端点。需要引入豁免机制。

---

## 2. ADR 决策记录

### ADR-001: auth_type 存储为 ENUM

**决策**: 采用 `ENUM('x_api_key', 'bearer')` 而非 `VARCHAR`。

**理由**: 认证方式是有限枚举值。ENUM 在 MySQL 8.0 中存储为 1-2 字节整数，节省空间。应用层可直接映射为 Pydantic `Literal` 类型。

### ADR-002: bearer 模式移除 anthropic-version 头部

**决策**: bearer 模式下不发送 `anthropic-version` 头部。

**理由**: 兼容模式的端点通常不识别 `anthropic-version`，保留可能导致 400 或被忽略。

### ADR-003: HTTP 豁免采用环境变量而非 per-agent 配置

**决策**: 使用 `AGENT_ALLOW_HTTP` 环境变量（布尔开关）。

**理由**: HTTP 豁免是运维级别的安全策略，应由部署者统一管控。若允许 per-agent HTTP，则任何前端用户都能绕过 SSRF 防护。

### ADR-004: 增量 ALTER 迁移

**决策**: 采用增量 ALTER TABLE ADD COLUMN，沿用现有 `_add_column_if_not_exists` 模式。

**理由**: 与项目现有迁移模式一致，对在线运行的服务零影响。

### ADR-005: auth_type 默认值为 x_api_key

**决策**: `auth_type` 默认值为 `'x_api_key'`（而非 NULL）。

**理由**: 向后兼容要求，现有 agent 记录自动保持原有行为。

---

## 3. 系统边界与目录映射

### 3.1 变更范围矩阵

| 文件 | 变更类型 | 变更内容 |
|------|---------|---------|
| `app/agent_client.py` | 修改 | 根据 auth_type 选择认证头部；HTTP 豁免逻辑 |
| `app/database.py` | 修改 | 白名单注册 auth_type；init_db() ALTER 迁移；CRUD 扩展 |
| `app/routers/agents.py` | 修改 | Pydantic Body 增加 auth_type；_agent_to_dict 增加 auth_type |
| `app/static/agents.html` | 修改 | 表单增加 auth_type 下拉；表格列增加认证方式 |
| `.env.example` | 修改 | 增加 AGENT_ALLOW_HTTP 说明 |

### 3.2 不变更文件

| 文件 | 原因 |
|------|------|
| `app/evolution.py` | 仅调用 `call_agent(usage, prompt)`，签名不变 |
| `app/report_engine.py` | 同上 |
| `app/auth.py` | 服务级鉴权不受影响 |
| `app/main.py` | 无需新路由或中间件 |
| `requirements.txt` | 无新增依赖 |

---

## 4. API 契约定义

### 4.1 POST /agents (CreateAgentBody 扩展)

```python
class CreateAgentBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1, max_length=100)
    version: str = Field(default="2023-06-01", max_length=50)
    auth_type: Literal["x_api_key", "bearer"] = Field(default="x_api_key")  # 新增
```

### 4.2 PUT /agents/{agent_id} (UpdateAgentBody 扩展)

```python
class UpdateAgentBody(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    version: str | None = None
    is_active: bool | None = None
    auth_type: Literal["x_api_key", "bearer"] | None = None  # 新增
```

### 4.3 GET /agents 响应扩展

`_agent_to_dict` 新增字段:

```python
"auth_type": a.get("auth_type", "x_api_key"),  # 兼容旧记录
```

### 4.4 异常码扩展

| HTTP 状态码 | 触发条件 | 错误消息 |
|-------------|---------|---------|
| 400 | bearer 模式 + HTTPS 强制 + 无豁免 | `"智能体 [{name}] base_url 必须使用 https 协议"` |
| 401 | API Key 无效 | `"智能体 [{name}] API Key 无效（401）"` |
| 403 | bearer 模式端点返回权限错误 | `"智能体 [{name}] 认证被拒绝（403）"` (新增) |
| 422 | auth_type 值非法 | Pydantic Literal 验证拒绝 |

---

## 5. 存储与状态流转设计

### 5.1 Schema 变更

```sql
ALTER TABLE `aso_agents` ADD COLUMN `auth_type`
  ENUM('x_api_key', 'bearer') NOT NULL DEFAULT 'x_api_key'
  AFTER `version`;
```

### 5.2 CRUD 函数扩展

- `get_all_agents()`: SELECT 列增加 `auth_type`
- `insert_agent()`: INSERT 列与值增加 `auth_type`
- `update_agent()`: `_ALLOWED_FIELDS` 增加 `"auth_type"`

### 5.3 状态流转

```
[x_api_key] ──── PUT /agents/{id} {auth_type: "bearer"} ────> [bearer]
[bearer]    ──── PUT /agents/{id} {auth_type: "x_api_key"} ──> [x_api_key]
```

无中间态，每次变更立即生效。

---

## 6. 时序与交互流

### 6.1 call_agent() 认证适配流程

```mermaid
sequenceDiagram
    participant Caller as 调用方(evolution/report_engine)
    participant AC as agent_client.call_agent()
    participant DB as database.get_assignment()
    participant Env as 环境变量
    participant API as LLM API 端点

    Caller->>AC: call_agent(usage, prompt, max_tokens)
    AC->>DB: get_assignment(usage)
    DB-->>AC: agent dict {auth_type, api_key_enc, base_url, ...}

    AC->>AC: decrypt_api_key(agent["api_key_enc"])
    AC->>AC: 解析 auth_type (默认 x_api_key)

    alt auth_type = x_api_key
        AC->>AC: 构建 headers = {x-api-key, anthropic-version}
    else auth_type = bearer
        AC->>AC: 构建 headers = {Authorization: Bearer ...}
    end

    AC->>AC: urlparse(base_url) scheme 检查

    alt scheme = https
        AC->>API: POST endpoint, headers, payload
    else scheme = http
        AC->>Env: 读取 AGENT_ALLOW_HTTP
        if AGENT_ALLOW_HTTP = true
            AC->>AC: 跳过 HTTPS 强制校验，仍检查内网地址
            AC->>API: POST endpoint, headers, payload
        else
            AC-->>Caller: ValueError("必须使用 https 协议")
        end
    end

    API-->>AC: HTTP Response

    alt status = 200
        AC->>AC: 解析 content 文本
        AC-->>Caller: result text
    else status = 401
        AC-->>Caller: ValueError("API Key 无效(401)")
    else status = 403
        AC-->>Caller: ValueError("认证被拒绝(403)")
    else 其他非200
        AC-->>Caller: ValueError("请求失败 {status}")
    end
```

---

## 7. Sprint 规划

### Sprint 1: 数据层与核心逻辑

| WBS | 任务 | 文件 | 优先级 |
|-----|------|------|--------|
| S1-1 | `_ALLOWED_COLUMNS` 白名单注册 `auth_type` | `app/database.py` | P0 |
| S1-2 | `init_db()` 中 `_new_columns` 增加 auth_type ALTER | `app/database.py` | P0 |
| S1-3 | `get_all_agents()` SELECT 增加 `auth_type` | `app/database.py` | P0 |
| S1-4 | `insert_agent()` 增加 auth_type 参数写入 | `app/database.py` | P0 |
| S1-5 | `update_agent()` `_ALLOWED_FIELDS` 增加 `auth_type` | `app/database.py` | P0 |
| S1-6 | `call_agent()` 认证头部适配 + HTTP 豁免 | `app/agent_client.py` | P0 |
| S1-7 | `call_agent()` 增加 403 状态码处理 | `app/agent_client.py` | P1 |

### Sprint 2: API 路由层

| WBS | 任务 | 文件 | 优先级 |
|-----|------|------|--------|
| S2-1 | `CreateAgentBody` 增加 `auth_type` 字段 | `app/routers/agents.py` | P0 |
| S2-2 | `UpdateAgentBody` 增加 `auth_type` 字段 | `app/routers/agents.py` | P0 |
| S2-3 | `_agent_to_dict()` 增加 `auth_type` 输出 | `app/routers/agents.py` | P0 |
| S2-4 | create/update endpoint 传递 auth_type | `app/routers/agents.py` | P0 |

### Sprint 3: 前端适配

| WBS | 任务 | 文件 | 优先级 |
|-----|------|------|--------|
| S3-1 | 表格列增加"认证方式"列 | `app/static/agents.html` | P0 |
| S3-2 | 新建弹窗增加 auth_type 下拉 | `app/static/agents.html` | P0 |
| S3-3 | 编辑弹窗回显 auth_type | `app/static/agents.html` | P0 |
| S3-4 | JS saveAgent/openEditModal 逻辑适配 | `app/static/agents.html` | P0 |
| S3-5 | Base URL placeholder 动态切换 | `app/static/agents.html` | P1 |

### Sprint 4: 环境变量与文档

| WBS | 任务 | 文件 | 优先级 |
|-----|------|------|--------|
| S4-1 | `.env.example` 增加 `AGENT_ALLOW_HTTP` | `.env.example` | P0 |
| S4-2 | startup 警告日志 | `app/main.py` | P1 |

---

## 附录 A: agent_client.py 改造伪代码

```python
import os
from typing import Literal

AuthType = Literal["x_api_key", "bearer"]

def call_agent(usage: str, prompt: str, max_tokens: int = 1000) -> str:
    agent = get_assignment(usage)
    if not agent:
        raise ValueError(...)

    api_key = decrypt_api_key(agent["api_key_enc"])
    base_url = agent["base_url"].rstrip("/")
    auth_type: AuthType = agent.get("auth_type") or "x_api_key"
    model = agent["model"]
    version = agent.get("version") or "2023-06-01"

    # SSRF 防护
    parsed = urlparse(base_url)
    allow_http = os.getenv("AGENT_ALLOW_HTTP", "").lower() in ("true", "1", "yes")
    if parsed.scheme == "http" and not allow_http:
        raise ValueError(f"智能体 [{agent['name']}] base_url 必须使用 https 协议")
    if parsed.scheme not in ("https", "http"):
        raise ValueError(f"智能体 [{agent['name']}] base_url 协议不支持: {parsed.scheme}")
    if _PRIVATE_HOST_RE.match(parsed.hostname or ""):
        raise ValueError(f"智能体 [{agent['name']}] base_url 不允许指向内网地址")

    endpoint = f"{base_url}/v1/messages"

    # 认证头部适配
    if auth_type == "bearer":
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    else:  # x_api_key (Anthropic 原生)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": version,
        }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)

    if resp.status_code == 401:
        raise ValueError(f"智能体 [{agent['name']}] API Key 无效（401）")
    if resp.status_code == 403:
        raise ValueError(f"智能体 [{agent['name']}] 认证被拒绝（403）")
    if resp.status_code == 404:
        raise ValueError(f"智能体 [{agent['name']}] 接口地址不存在（404）")
    if resp.status_code != 200:
        raise ValueError(f"智能体 [{agent['name']}] 请求失败 {resp.status_code}")

    content = resp.json().get("content", [])
    texts = [b["text"] for b in content if b.get("type") == "text"]
    result = "\n".join(texts).strip()
    if not result:
        raise ValueError(f"智能体 [{agent['name']}] 返回空内容")
    return result
```

## 附录 B: 向后兼容保障清单

| 场景 | 预期行为 | 验证方法 |
|------|---------|---------|
| 现有 agent 记录无 auth_type 字段 | 自动视为 `x_api_key` | `agent.get("auth_type") or "x_api_key"` |
| DB ALTER 后旧行 auth_type 为 DEFAULT | 值为 `'x_api_key'`，行为不变 | SELECT 验证 |
| 前端不传 auth_type 参数 | Pydantic default="x_api_key" 生效 | POST 不带 auth_type 字段 |
| 前端传入非法 auth_type 值 | Pydantic Literal 验证拒绝，返回 422 | POST {auth_type: "oauth"} |
| AGENT_ALLOW_HTTP 未设置 | HTTP 端点仍被拒绝（默认安全） | 不设环境变量 + HTTP base_url |
| bearer 模式 + https 端点 | 正常通过 SSRF 校验 | bearer + https base_url |
