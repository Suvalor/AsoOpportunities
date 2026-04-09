"""
配置模块：定义行为动词 × 生活场景矩阵，生成种子词列表。
"""

import itertools

# 人在手机上做的事（行为动词）
VERBS = [
    "track",
    "log",
    "calculate",
    "convert",
    "remind",
    "scan",
    "find",
    "manage",
    "plan",
    "monitor",
    "schedule",
    "compare",
    "generate",
    "split",
    "estimate",
]

# 具体生活情境（不是品类，而是场景）
SCENES = [
    "medication",
    "lease",
    "tip",
    "shift",
    "period",
    "sleep",
    "invoice",
    "mileage",
    "loan",
    "symptom",
    "habit",
    "chore",
    "receipt",
    "interview",
    "rent",
    "subscription",
]

# 笛卡尔积生成 240 个种子词
SEEDS: list[str] = [f"{verb} {scene}" for verb, scene in itertools.product(VERBS, SCENES)]

# --- 运行参数 ---

# iTunes / Autocomplete 的市场区域
COUNTRY = "us"

# 每次 API 请求之间的间隔（秒），避免触发速率限制
RATE_LIMIT_SLEEP = 0.5

# Autocomplete 每个种子词最多返回多少补全词
AUTOCOMPLETE_LIMIT = 20

# iTunes Search 每个关键词返回多少结果（用于评估竞争度）
ITUNES_LIMIT = 10
