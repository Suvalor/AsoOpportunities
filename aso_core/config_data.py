"""
种子矩阵：行为动词 × 生活场景 → 种子词列表（唯一真源）。
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
