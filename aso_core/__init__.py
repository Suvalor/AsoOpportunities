"""
ASO 核心逻辑包：采集、竞争评估、扫描流水线、蓝海评分与统一配置。
"""

from .config_data import SEEDS, SCENES, VERBS
from .scanner import run_full_scan
from .scorer import blue_ocean_label, blue_ocean_score
from .settings import Settings, get_settings

__all__ = [
    "SEEDS",
    "SCENES",
    "VERBS",
    "Settings",
    "get_settings",
    "run_full_scan",
    "blue_ocean_score",
    "blue_ocean_label",
]
