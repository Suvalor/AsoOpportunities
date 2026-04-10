# Claude 二次筛选 Prompt 模板

> 原 `claude_prompt.py` 的核心内容。该脚本读取 `aso_opportunities.csv`，
> 按 `opportunity_score` 取 Top N 条关键词，拼成以下 Prompt 模板后粘贴给 Claude 进行二次筛选。

---

## Prompt 正文

你是一位有 10 年经验的移动应用产品经理，擅长评估 App Store 市场机会。

以下是通过 Apple Autocomplete API + iTunes Search API 挖掘出的关键词机会列表，
已按「搜索量代理 / 竞争强度」得分从高到低排序。

每行格式：

```
排名 | 关键词 | 机会分 | 补全排名 | 头部App评论数
```

请对**每一个**关键词完成以下四项分析：

1. **用户痛点**：这个搜索词背后，用户具体想解决什么问题？
2. **需求频率**：是「一次性需求」还是「高频刚需（每周/每天用）」？
3. **付费意愿**：用户是否有天然的付费动机？（订阅 / 买断 / 免费工具）
4. **综合优先级**：综合以上三点，给出 1-5 分（5=强烈推荐立即做，1=意义不大）

请以 Markdown 表格输出，列名为：

| 关键词 | 用户痛点 | 需求频率 | 付费意愿 | 优先级(1-5) | 理由（一句话） |
|--------|----------|----------|----------|-------------|----------------|

---

**（此处插入关键词列表，格式示例）**

```
  1. track tips                         机会分= 85.20  补全排名= 1  头部App评论=   12,345
  2. life track-medication reminder      机会分= 78.50  补全排名= 2  头部App评论=    8,900
  ...
```

---

完成表格后，请额外输出：
- **强烈推荐（优先级 4-5）的关键词汇总**，并说明为什么这些词适合做成独立 App
- **需要警惕的关键词**（机会分高但实际价值低的词），说明原因

---

## 原始用法

该 Prompt 原本通过 CLI 脚本生成：

```bash
python claude_prompt.py              # 默认读 aso_opportunities.csv，取 Top 50
python claude_prompt.py --top 30     # 只取 Top 30
python claude_prompt.py --input results.csv --top 100
python claude_prompt.py --out prompt.txt  # 将 prompt 写入文件
```

项目重组后该脚本已移除，Prompt 模板保留于此文档供手动使用。
