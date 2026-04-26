"""
种子矩阵：痛点场景种子词列表（唯一真源）。

仅用于首次部署 bootstrap，种子后续由 aso_seeds 表管理。
v3：从笛卡尔积种子改为结构化 SeedEntry，含 category 与 confidence，
    模拟真实 App Store 搜索意图。
"""

from __future__ import annotations

from typing import TypedDict


class SeedEntry(TypedDict):
    seed: str
    category: str       # "pain_point" | "category_word" | "trend_word"
    confidence: float   # 0.0-1.0


SEEDS_V2: list[SeedEntry] = [
    # ── 生产力/工具 ──────────────────────────────────────────────
    {"seed": "split rent with roommate", "category": "pain_point", "confidence": 0.95},
    {"seed": "track freelance income for tax", "category": "pain_point", "confidence": 0.90},
    {"seed": "calculate tip split group", "category": "pain_point", "confidence": 0.85},
    {"seed": "scan receipt for expense report", "category": "pain_point", "confidence": 0.88},
    {"seed": "log work hours for payroll", "category": "pain_point", "confidence": 0.85},
    {"seed": "cancel subscription before trial ends", "category": "pain_point", "confidence": 0.82},
    {"seed": "convert currency without internet", "category": "pain_point", "confidence": 0.80},
    {"seed": "remind pill when to take", "category": "pain_point", "confidence": 0.90},
    {"seed": "plan weekly meal prep on budget", "category": "pain_point", "confidence": 0.85},
    {"seed": "estimate home repair cost before hiring", "category": "pain_point", "confidence": 0.78},
    # ── 健康/生活 ────────────────────────────────────────────────
    {"seed": "track symptom for doctor visit", "category": "pain_point", "confidence": 0.92},
    {"seed": "log blood pressure daily trend", "category": "pain_point", "confidence": 0.88},
    {"seed": "improve sleep quality naturally", "category": "pain_point", "confidence": 0.85},
    {"seed": "track habit streak without breaking", "category": "pain_point", "confidence": 0.82},
    {"seed": "predict period and fertility window", "category": "pain_point", "confidence": 0.90},
    {"seed": "remind medication schedule elderly", "category": "pain_point", "confidence": 0.88},
    {"seed": "log migraine trigger and pattern", "category": "pain_point", "confidence": 0.85},
    # ── 财务/商务 ────────────────────────────────────────────────
    {"seed": "split bill with uneven shares", "category": "pain_point", "confidence": 0.88},
    {"seed": "track mileage for tax deduction", "category": "pain_point", "confidence": 0.90},
    {"seed": "calculate loan payoff early", "category": "pain_point", "confidence": 0.82},
    {"seed": "send invoice for freelance work", "category": "pain_point", "confidence": 0.85},
    {"seed": "estimate tax deduction freelance", "category": "pain_point", "confidence": 0.80},
    {"seed": "compare insurance quote side by side", "category": "pain_point", "confidence": 0.78},
    {"seed": "split grocery cost with roommate", "category": "pain_point", "confidence": 0.82},
    # ── 旅行/本地 ────────────────────────────────────────────────
    {"seed": "find parking near concert venue", "category": "pain_point", "confidence": 0.80},
    {"seed": "plan road trip with stops", "category": "pain_point", "confidence": 0.82},
    {"seed": "compare flight price flexible dates", "category": "pain_point", "confidence": 0.85},
    {"seed": "rent pickup truck for moving", "category": "pain_point", "confidence": 0.80},
    {"seed": "find cheap gas on route", "category": "pain_point", "confidence": 0.78},
    # ── 教育/学习 ────────────────────────────────────────────────
    {"seed": "study flashcards for medical exam", "category": "pain_point", "confidence": 0.85},
    {"seed": "create study schedule for finals", "category": "pain_point", "confidence": 0.82},
    {"seed": "practice language daily with reminder", "category": "pain_point", "confidence": 0.80},
    # ── 家居/生活 ────────────────────────────────────────────────
    {"seed": "track chore for family members", "category": "pain_point", "confidence": 0.78},
    {"seed": "plan grocery list from recipes", "category": "pain_point", "confidence": 0.80},
    {"seed": "remind me to water plants", "category": "pain_point", "confidence": 0.75},
    # ── 品类词（category_word）──────────────────────────────────
    {"seed": "budget planner app", "category": "category_word", "confidence": 0.70},
    {"seed": "habit tracker daily", "category": "category_word", "confidence": 0.65},
    {"seed": "medication reminder alarm", "category": "category_word", "confidence": 0.70},
    {"seed": "expense tracker business", "category": "category_word", "confidence": 0.68},
    {"seed": "sleep tracker analysis", "category": "category_word", "confidence": 0.65},
    {"seed": "invoice generator small business", "category": "category_word", "confidence": 0.70},
    {"seed": "mileage log reimbursement", "category": "category_word", "confidence": 0.68},
    {"seed": "period tracker fertility", "category": "category_word", "confidence": 0.72},
    {"seed": "recipe meal planner", "category": "category_word", "confidence": 0.65},
    {"seed": "rent split calculator", "category": "category_word", "confidence": 0.70},
    # ── 趋势词（trend_word）─────────────────────────────────────
    {"seed": "ai resume builder", "category": "trend_word", "confidence": 0.60},
    {"seed": "remote work time tracker", "category": "trend_word", "confidence": 0.55},
    {"seed": "carbon footprint calculator", "category": "trend_word", "confidence": 0.58},
    {"seed": "meal prep for weight loss", "category": "trend_word", "confidence": 0.62},
    {"seed": "crypto portfolio tracker tax", "category": "trend_word", "confidence": 0.55},
]

# 向后兼容：旧代码仍可 from aso_core.config_data import SEEDS
SEEDS: list[str] = [e["seed"] for e in SEEDS_V2]
