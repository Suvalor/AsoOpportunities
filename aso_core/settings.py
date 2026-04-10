"""
统一配置加载。

优先级（后者覆盖前者）：
  1. 代码内默认值
  2. 可选 config.json（路径由 ASO_CONFIG_JSON 指定，默认 ./config.json）浅层合并
  3. 环境变量（含 .env 通过 load_dotenv 注入，且不覆盖已存在的环境变量）。
     主市场由 ASO_PRIMARY_COUNTRY 指定（扫描国家列表见 aso_core.scanner.SCAN_COUNTRIES）。

说明：种子词列表 SEEDS 仅在 config_data 中维护，不由 json 覆盖。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """运行时可调参数（不含种子矩阵）。"""

    default_country: str
    rate_limit_sleep: float
    autocomplete_limit: int
    itunes_limit: int
    rank_history_path: Path


def _json_overrides() -> dict:
    path = Path(os.getenv("ASO_CONFIG_JSON", "config.json"))
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_settings() -> Settings:
    """读取当前生效配置（每次调用重新解析，便于测试与热读 json）。"""
    load_dotenv(override=False)

    defaults: dict = {
        "default_country": "us",
        "rate_limit_sleep": 0.5,
        "autocomplete_limit": 20,
        "itunes_limit": 10,
        "rank_history_path": "rank_history.json",
    }
    merged = {**defaults, **_json_overrides()}

    # 主市场：与 ASO_PRIMARY_COUNTRY 一致；兼容旧环境变量 COUNTRY
    country = os.getenv("ASO_PRIMARY_COUNTRY") or os.getenv("COUNTRY")
    if country is None:
        country = str(merged.get("default_country") or defaults["default_country"])

    rls = os.getenv("RATE_LIMIT_SLEEP")
    if rls is not None:
        rate_limit_sleep = float(rls)
    else:
        rate_limit_sleep = float(merged.get("rate_limit_sleep", defaults["rate_limit_sleep"]))

    al = os.getenv("AUTOCOMPLETE_LIMIT")
    if al is not None:
        autocomplete_limit = int(al)
    else:
        autocomplete_limit = int(merged.get("autocomplete_limit", defaults["autocomplete_limit"]))

    il = os.getenv("ITUNES_LIMIT")
    if il is not None:
        itunes_limit = int(il)
    else:
        itunes_limit = int(merged.get("itunes_limit", defaults["itunes_limit"]))

    rhp = os.getenv("RANK_HISTORY_PATH")
    if rhp is not None:
        rank_history_path = Path(rhp)
    else:
        rank_history_path = Path(
            str(merged.get("rank_history_path", defaults["rank_history_path"]))
        )

    return Settings(
        default_country=country.strip().lower() or "us",
        rate_limit_sleep=rate_limit_sleep,
        autocomplete_limit=autocomplete_limit,
        itunes_limit=itunes_limit,
        rank_history_path=rank_history_path,
    )
