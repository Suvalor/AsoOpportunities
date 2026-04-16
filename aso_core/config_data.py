"""
种子矩阵：痛点场景种子词列表（唯一真源）。

仅用于首次部署 bootstrap，种子后续由 aso_seeds 表管理。
v2：从"通用动词×场景"笛卡尔积改为聚焦痛点场景词，模拟真实 App Store 搜索意图。
"""

# 痛点场景种子：模拟真实 App Store 搜索意图
SEEDS: list[str] = [
    # 生产力/工具
    "split rent with roommate",
    "track freelance income",
    "calculate tip split",
    "scan receipt for expense",
    "log work hours shift",
    "manage subscription cancel",
    "convert currency travel",
    "remind medication schedule",
    "plan weekly meal prep",
    "estimate home repair cost",
    # 健康/生活
    "track symptom chronic",
    "log blood pressure",
    "monitor sleep quality",
    "track habit streak",
    "period tracker fertility",
    "calculate water intake",
    "remind pill vitamin",
    "log migraine trigger",
    # 财务/商务
    "split bill group",
    "track mileage reimbursement",
    "calculate loan payoff",
    "manage invoice small business",
    "estimate tax deduction",
    "compare insurance quote",
    "track expense category",
    "split grocery cost",
    # 旅行/本地
    "find parking near me",
    "plan road trip route",
    "compare flight price",
    "rent truck moving",
    "find cheap gas station",
    "schedule delivery time",
    # 教育/学习
    "flashcard study app",
    "calculate gpa grade",
    "convert unit measurement",
    "practice language daily",
    "schedule study plan",
    # 家居/生活
    "track chore family",
    "plan grocery list",
    "monitor plant watering",
    "schedule home cleaning",
    "compare rent neighborhood",
    # 社交/沟通
    "split dinner bill",
    "find volunteer opportunity",
    "schedule group event",
    "plan trip with friends",
]
