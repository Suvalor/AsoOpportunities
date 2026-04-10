# n8n Workflow 导入说明

本目录用于存放 n8n 工作流的 JSON 导出文件。

## 推荐的两个 Workflow

| Workflow | 用途 | 触发方式 |
|----------|------|----------|
| workflow-scan.json | 定时全量扫描 + 每日追踪 | Cron 节点 |
| workflow-report.json | 拉取分析数据 → AI 生成报告 → 推送飞书 | Cron 节点 |

## 如何从 n8n 导出

1. 打开 n8n 编辑器，进入目标 Workflow
2. 点击右上角 **...** → **Export**
3. 选择 **Download as JSON**
4. 将下载的 JSON 文件放入本目录，命名为上表中的文件名

## 如何导入到新的 n8n 实例

1. 打开 n8n 编辑器
2. 点击左侧 **Workflows** → **Import from File**
3. 选择本目录下的 JSON 文件
4. 修改以下配置：
   - **HTTP Request 节点**：将 URL 改为你的 aso-service 地址（如 `http://aso-service:8000`）
   - **Header Auth**：设置 `X-API-Key` 为你的 `API_KEY`
   - **飞书节点**：修改 Webhook 地址为你的飞书机器人 URL
5. 激活 Workflow

## 环境变量

在 n8n 环境中设置以下变量供 Workflow 引用：

```
ASO_API_KEY=你的API_KEY
ASO_SERVICE_URL=http://aso-service:8000
FEISHU_WEBHOOK_URL=你的飞书Webhook地址
```
