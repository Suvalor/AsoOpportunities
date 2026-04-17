# 漏洞扫描和缺陷扫描 — 需求文档

## 核心目标
对 ASO Keyword Engine 项目进行全面安全漏洞和代码缺陷扫描，发现并修复所有问题。

## 扫描范围

### Python 后端
- `app/database.py` — SQL 注入、资源泄漏
- `app/auth.py` — 认证绕过、JWT 问题
- `app/user_auth.py` — 密码处理、Token 安全
- `app/routers/*.py` — 输入验证、权限控制
- `app/agent_client.py` — API Key 泄露、SSRF
- `app/evolution.py` — 逻辑缺陷
- `app/report_engine.py` — 数据处理缺陷
- `app/main.py` — 配置安全
- `aso_core/*.py` — 算道安全

### 前端
- `app/static/*.html` — XSS、Token 处理、敏感数据暴露

## 发现的问题

### Critical / High

1. **SQL 注入风险** — `_add_column_if_not_exists()` 使用 f-string 拼接 table/column 名
   - 文件: `app/database.py:128`
   - `f"ALTER TABLE \`{table}\` ADD COLUMN \`{column}\` {definition}"`
   - 虽然 table/column 来自内部调用，但违反参数化查询原则

2. **SQL 注入风险** — `update_job()` 和 `update_agent()` 使用 f-string 构建 SET 子句
   - 文件: `app/database.py:384`, `app/database.py:1636`
   - 虽然字段名来自内部代码，但模式不安全

3. **SSRF 风险** — `agent_client.py` 的 `base_url` 来自数据库，无校验
   - 文件: `app/agent_client.py:29`
   - 攻击者若获得 admin 权限可设置 base_url 指向内网

4. **JWT 无算法校验** — `decode_token()` 未指定 algorithms 白名单
   - 文件: `app/user_auth.py:41`
   - `jwt.decode(token, JWT_SECRET, algorithms=["HS256"])` 已指定，但应防范 none 算法攻击

5. **Cookie 缺少 Secure 标志** — JWT cookie 未设置 `secure=True`
   - 文件: `app/routers/auth_router.py:65`
   - HTTP 下 cookie 可被中间人截获

### Medium

6. **API Key 时序攻击** — `verify_api_key()` 使用 `!=` 比较 API Key
   - 文件: `app/auth.py:21`
   - 应使用 `hmac.compare_digest()` 防止时序攻击

7. **API Key 时序攻击** — `verify_api_key_or_cookie()` 同样使用 `!=`
   - 文件: `app/auth.py:50`

8. **异常信息泄露** — `agent_client.py` 在错误消息中包含 `base_url` 和 `model`
   - 文件: `app/agent_client.py:51-52`
   - 内部配置信息泄露给调用方

9. **异常信息泄露** — `routers/agents.py` 在 503 错误中暴露 MySQL 错误详情
   - 文件: `app/routers/agents.py:128-129`

10. **无速率限制** — 登录接口无暴力破解防护
    - 文件: `app/routers/auth_router.py:50`

11. **无速率限制** — 注册接口无防护
    - 文件: `app/routers/auth_router.py:28`

12. **`TRACKING_MIN_BLUE_SCORE` 硬编码** — scan.py 中使用 60，与新阈值 55 不一致
    - 文件: `app/routers/scan.py:33`

13. **`get_keyword_snapshot_for_report` 硬编码 60** — 报告快照阈值与新蓝海阈值 55 不一致
    - 文件: `app/database.py:1389,1401`

### Low

14. **`_is_too_similar` 未考虑词序** — "rent truck" 和 "truck rent" Jaccard=1.0 但语义不同
    - 文件: `app/evolution.py` (新代码)

15. **`opportunity_score` 边界** — `exp(0.0002 * 0) = 1.0`，当 top_reviews=0 时 volume_proxy/1.0 合理但 count_penalty=1.0 可能偏低
    - 文件: `aso_core/competition.py`

## 验收标准
1. 所有 Critical/High 问题已修复
2. 所有 Medium 问题已修复或已标注为已知风险
3. 修复后语法检查通过
4. 不引入新漏洞
