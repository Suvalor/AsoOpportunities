"""
种子矩阵自迭代：评估表现、剪枝、LLM 生成 pending 种子、校验后激活。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

from aso_core.autocomplete import get_autocomplete
from aso_core.settings import get_settings

from .database import (
    activate_seed,
    append_evolution_log,
    fetch_pending_seeds_ordered,
    get_active_seeds,
    get_seed_performance_by_batch,
    get_top_keywords_for_batch,
    insert_pending_seeds,
    max_seed_generation,
    set_seeds_pruned,
)

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_VERSION = "2023-06-01"

# 剪枝：本批次内表现过差且矩阵仍保留足够 active 时才剪
_MIN_ACTIVE_AFTER_PRUNE = 8
_WEAK_AVG_SCORE = 40.0
_WEAK_MIN_KEYWORDS = 3

# 待激活种子校验
_SEED_LEN_MIN = 2
_SEED_LEN_MAX = 80


def evaluate_seed_performance(batch_id: str) -> None:
    """
    按 scan_batch_id 聚合各 seed 的关键词表现，并写入进化日志（只追加）。
    """
    rows = get_seed_performance_by_batch(batch_id)
    payload = {
        "seed_count": len(rows),
        "per_seed": [
            {
                "seed": r["seed"],
                "keyword_count": int(r["keyword_count"] or 0),
                "avg_blue_ocean_score": float(r["avg_blue_ocean_score"] or 0),
                "max_blue_ocean_score": int(r["max_blue_ocean_score"] or 0),
                "strong_count": int(r["strong_count"] or 0),
            }
            for r in rows
        ],
    }
    append_evolution_log(batch_id, "evaluate_seed_performance", payload)
    logger.info(
        "evaluate_seed_performance batch_id=%s 聚合 %d 个种子",
        batch_id,
        len(rows),
    )


def prune_weak_seeds(batch_id: str) -> list[str]:
    """
    依据本批次聚合结果剪枝：强词数为 0、均分偏低且样本足够时标记 pruned。
    若当前 active 总数过少则跳过剪枝，避免矩阵被清空。
    """
    stats = get_seed_performance_by_batch(batch_id)
    active_now = get_active_seeds()
    if len(active_now) <= _MIN_ACTIVE_AFTER_PRUNE:
        append_evolution_log(
            batch_id,
            "prune_skipped",
            {"reason": "active_count_too_low", "active_count": len(active_now)},
        )
        return []

    candidates: list[tuple[str, float]] = []
    for r in stats:
        seed = str(r["seed"])
        kw_n = int(r["keyword_count"] or 0)
        strong = int(r["strong_count"] or 0)
        avg = float(r["avg_blue_ocean_score"] or 0)
        if strong == 0 and avg < _WEAK_AVG_SCORE and kw_n >= _WEAK_MIN_KEYWORDS:
            candidates.append((seed, avg))

    candidates.sort(key=lambda x: x[1])
    max_prune = max(0, len(active_now) - _MIN_ACTIVE_AFTER_PRUNE)
    to_prune = [s for s, _ in candidates[:max_prune]]

    if to_prune:
        set_seeds_pruned(to_prune)
    append_evolution_log(
        batch_id,
        "prune_weak_seeds",
        {"pruned": to_prune, "candidates_considered": len(candidates)},
    )
    logger.info("prune_weak_seeds batch_id=%s 剪枝 %d 条", batch_id, len(to_prune))
    return to_prune


def _call_anthropic_messages(user_text: str) -> tuple[str, str | None]:
    """调用 Anthropic Messages API，返回 (assistant 文本, 错误说明)。"""
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        return "", "missing_ANTHROPIC_API_KEY"

    body: dict[str, Any] = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": user_text}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers=headers,
            json=body,
            timeout=120,
        )
        if resp.status_code >= 400:
            return "", f"http_{resp.status_code}:{resp.text[:500]}"
        data = resp.json()
        parts = data.get("content") or []
        texts: list[str] = []
        for p in parts:
            if isinstance(p, dict) and p.get("type") == "text":
                texts.append(str(p.get("text") or ""))
        return "\n".join(texts).strip(), None
    except Exception as exc:
        logger.exception("Anthropic API 调用异常: %s", exc)
        return "", str(exc)[:500]


def _parse_seed_list_from_llm(text: str) -> list[str]:
    """从模型输出中解析 JSON 数组或逐行种子列表。"""
    text = text.strip()
    if not text:
        return []
    # 尝试提取 JSON 数组
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            raw = json.loads(m.group(0))
            if isinstance(raw, list):
                return [str(x).strip() for x in raw if str(x).strip()]
        except json.JSONDecodeError:
            pass
    # 回退：非空行
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip().strip("-*•").strip()
        if 2 <= len(s) <= _SEED_LEN_MAX:
            out.append(s)
    return out


def generate_new_seeds(batch_id: str, top_n: int = 10) -> tuple[list[str], str]:
    """
    基于本批次高分关键词调用 Claude 生成新种子，仅写入 pending。
    返回 (本次尝试写入的种子短语列表去重后, 说明字符串)。
    """
    top_n = min(max(top_n, 1), 50)
    top_rows = get_top_keywords_for_batch(batch_id, top_n)
    if not top_rows:
        msg = "no_keywords_in_batch"
        append_evolution_log(batch_id, "generate_new_seeds_skipped", {"reason": msg})
        return [], msg

    lines = []
    for r in top_rows:
        lines.append(
            f"- keyword={r.get('keyword')} score={r.get('blue_ocean_score')} "
            f"seed={r.get('seed')} flags={r.get('blue_ocean_flags')}"
        )
    user_prompt = (
        "你是 ASO 关键词研究助手。根据下列高蓝海分关键词，提出 "
        f"{top_n} 个以内、简短英文或常见 App Store 搜索风格的「种子词/短语」，"
        "用于后续 autocomplete 扩展；避免与列表中已有 keyword 完全重复，"
        "不要解释，只输出一个 JSON 字符串数组，例如 [\"foo bar\",\"baz\"]。\n\n"
        + "\n".join(lines)
    )

    assistant_text, err = _call_anthropic_messages(user_prompt)
    if err:
        append_evolution_log(
            batch_id,
            "generate_new_seeds_failed",
            {"error": err},
        )
        return [], err

    parsed = _parse_seed_list_from_llm(assistant_text)
    seen: set[str] = set()
    cleaned: list[str] = []
    for s in parsed:
        s = s.strip()
        if not s or len(s) > _SEED_LEN_MAX or len(s) < _SEED_LEN_MIN:
            continue
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        cleaned.append(s)
        if len(cleaned) >= top_n:
            break

    if not cleaned:
        append_evolution_log(
            batch_id,
            "generate_new_seeds_empty",
            {"raw_preview": assistant_text[:800]},
        )
        return [], "parsed_empty"

    gen = max_seed_generation() + 1
    insert_pending_seeds(cleaned, generation=gen, source="generated")
    append_evolution_log(
        batch_id,
        "generate_new_seeds",
        {
            "generation": gen,
            "requested_top_n": top_n,
            "seeds": cleaned,
            "raw_preview": assistant_text[:1200],
        },
    )
    logger.info(
        "generate_new_seeds batch_id=%s 写入 pending %d 条 generation=%d",
        batch_id,
        len(cleaned),
        gen,
    )
    return cleaned, "ok"


def _seed_passes_autocomplete(seed: str) -> bool:
    """校验：App Store autocomplete 能返回至少一条结果。"""
    s = get_settings()
    try:
        comps = get_autocomplete(seed, country=s.default_country, sleep=0.3)
        return len(comps) > 0
    except Exception as exc:
        logger.warning("validate 种子 autocomplete 失败 [%s]: %s", seed, exc)
        return False


def validate_pending_seeds(max_validate: int = 5) -> None:
    """
    按创建时间顺序校验 pending 种子；通过 autocomplete 检测的最多激活 max_validate 条。
    每次全量扫描后调用时，max_validate=5 与产品约束一致。
    """
    max_validate = min(max(max_validate, 0), 50)
    pending = fetch_pending_seeds_ordered(limit=200)
    activated: list[str] = []
    skipped: list[str] = []

    for seed in pending:
        if len(activated) >= max_validate:
            break
        if not (_SEED_LEN_MIN <= len(seed) <= _SEED_LEN_MAX):
            skipped.append(seed)
            continue
        if not _seed_passes_autocomplete(seed):
            skipped.append(seed)
            continue
        if activate_seed(seed):
            activated.append(seed)

    append_evolution_log(
        None,
        "validate_pending_seeds",
        {
            "max_validate": max_validate,
            "activated": activated,
            "skipped_count": len(skipped),
        },
    )
    logger.info(
        "validate_pending_seeds 激活 %d 条 pending",
        len(activated),
    )


def run_evolution_after_full_scan(batch_id: str) -> None:
    """
    全量扫描写库后的进化流水线顺序：评估 → 剪枝 → 生成 → 校验。
    单步失败仅记日志，不中断后续步骤。
    """
    try:
        evaluate_seed_performance(batch_id)
    except Exception as exc:
        logger.exception("evaluate_seed_performance 失败: %s", exc)

    try:
        prune_weak_seeds(batch_id)
    except Exception as exc:
        logger.exception("prune_weak_seeds 失败: %s", exc)

    try:
        generate_new_seeds(batch_id, top_n=10)
    except Exception as exc:
        logger.exception("generate_new_seeds 失败: %s", exc)

    try:
        validate_pending_seeds(max_validate=5)
    except Exception as exc:
        logger.exception("validate_pending_seeds 失败: %s", exc)
