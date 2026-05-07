# 设计规格书：智能体认证方式适配 (auth_type)

> 文档版本: 1.0.0
> 日期: 2026-04-26
> 状态: 已确认

---

## 1. Design System Audit

### 1.1 现有 UI 框架

- **技术栈**: Vanilla JS + 原生 CSS，无构建工具
- **主题**: 暗色主题，CSS 变量驱动 (`--bg-primary`, `--text-primary` 等)
- **组件模式**: 模态框 (modal) + 表格 (table) + 表单 (form-group)
- **交互方式**: fetch API + DOM 操作，无框架绑定

### 1.2 现有 agents.html 组件清单

| 组件 | 选择器/ID | 用途 |
|------|----------|------|
| 智能体表格 | `#agentsTable` | 展示所有智能体列表 |
| 新建弹窗 | `#createModal` | 创建智能体表单 |
| 编辑弹窗 | `#editModal` | 编辑智能体表单 |
| 表单字段 | `.form-group` | name, base_url, api_key, model, version |
| 分配列表 | `#assignmentsList` | 用途分配管理 |

### 1.3 现有表单字段顺序

1. 名称 (name)
2. Base URL (base_url)
3. API Key (api_key)
4. 模型 (model)
5. 版本 (version)

---

## 2. 业务逻辑建模

### 2.1 字段校验规则

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|---------|
| auth_type | ENUM | 是 | 仅允许 `x_api_key` 或 `bearer` |
| base_url | string | 是 | 需通过 SSRF 校验；http 需 AGENT_ALLOW_HTTP=true |

### 2.2 逻辑分支

```
IF auth_type == "x_api_key":
    headers = { x-api-key, anthropic-version }
ELSE IF auth_type == "bearer":
    headers = { Authorization: Bearer }
    (不发送 anthropic-version)
```

### 2.3 数据约束

- auth_type 默认值: `x_api_key`
- ENUM 值不可为空
- bearer 模式下 version 字段仍存储但不使用

---

## 3. 全路径业务流

```mermaid
flowchart TD
    A[用户打开智能体管理页] --> B{操作类型}
    B -->|新建| C[打开新建弹窗]
    B -->|编辑| D[打开编辑弹窗]

    C --> E[填写表单]
    E --> F[选择认证方式]
    F --> G{auth_type?}
    G -->|x_api_key| H[Base URL placeholder: https://api.anthropic.com]
    G -->|bearer| I[Base URL placeholder: https://openrouter.ai/api]
    H --> J[提交表单]
    I --> J

    D --> K[回显当前 auth_type]
    K --> L[修改认证方式]
    L --> M[Base URL placeholder 动态切换]
    M --> N[提交表单]

    J --> O[POST /agents]
    N --> P[PUT /agents/{id}]

    O --> Q{auth_type 合法?}
    P --> Q
    Q -->|是| R[写入 DB]
    Q -->|否| S[422 校验错误]

    R --> T[刷新列表]
```

---

## 4. 交互规格说明

### 4.1 认证方式下拉组件

**位置**: Base URL 字段之后、API Key 字段之前

**HTML 结构**:
```html
<div class="form-group">
    <label for="fAuthType">认证方式</label>
    <select id="fAuthType">
        <option value="x_api_key">Anthropic 原生 (x-api-key)</option>
        <option value="bearer">兼容模式 (Bearer Token)</option>
    </select>
    <div class="form-hint">Anthropic 原生使用 x-api-key 头部；兼容模式使用 Authorization: Bearer 头部，适配 OpenRouter 等服务</div>
</div>
```

**交互规则**:
- 新建时默认选中 `x_api_key`
- 编辑时回显当前值
- 切换选项时动态更新 Base URL placeholder

### 4.2 Base URL Placeholder 动态切换

| auth_type 值 | placeholder 文本 |
|-------------|-----------------|
| `x_api_key` | `https://api.anthropic.com` |
| `bearer` | `https://openrouter.ai/api` |

**触发**: auth_type 下拉 change 事件

### 4.3 表格认证方式列

在"版本"列之后新增"认证方式"列：

| 列顺序 | 列名 | 显示内容 |
|-------|------|---------|
| ... | 版本 | version 值 |
| 新增 | 认证方式 | `x_api_key` → "Anthropic 原生"，`bearer` → "兼容模式" |
| ... | 状态 | is_active 状态 |

---

## 5. UI 状态矩阵

### 5.1 认证方式下拉

| 状态 | 表现 |
|------|------|
| Default | 显示当前选中值，下拉箭头 |
| Hover | 选项高亮 |
| Active/Selected | 选中项有勾选标记 |
| Disabled | 不适用（此字段始终可编辑） |
| Loading | 不适用（无异步加载） |
| Error | 不适用（Pydantic 校验在提交时触发） |

### 5.2 新建弹窗

| 状态 | 表现 |
|------|------|
| Default | 所有字段为空/默认值，auth_type 默认 x_api_key |
| Submitting | 按钮显示 loading 状态 |
| Success | 弹窗关闭，表格刷新，显示成功提示 |
| Error | 显示错误信息，弹窗不关闭 |

### 5.3 编辑弹窗

| 状态 | 表现 |
|------|------|
| Default | 回显当前 agent 所有字段值，包括 auth_type |
| Submitting | 按钮显示 loading 状态 |
| Success | 弹窗关闭，表格刷新 |
| Error | 显示错误信息 |

---

## 6. 无障碍与响应式检查

### 6.1 无障碍

- `<select>` 元素需关联 `<label>` (for/id 绑定)
- 下拉选项文本需清晰描述含义
- form-hint 使用 `aria-describedby` 关联到 select

### 6.2 响应式

- 新增的下拉组件与现有 form-group 样式一致，自动继承响应式布局
- 表格新增列在小屏幕下可能需要水平滚动（与现有行为一致）
