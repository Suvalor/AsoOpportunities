"""
统一智能体调用入口：从数据库读取配置，发起 LLM API 请求。
支持 Anthropic 原生协议 (x-api-key) 和兼容协议 (Bearer Token)。
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .database import decrypt_api_key, get_assignment

logger = logging.getLogger(__name__)

# base_url 安全校验：禁止内网地址
_PRIVATE_HOST_RE = re.compile(
    r"^(127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.|localhost|::1|fe80:)",
    re.IGNORECASE,
)

# 瞬态错误状态码：触发指数退避重试
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}

# 退避参数：环境变量可覆盖，有最小值保底
_MAX_RETRIES = max(0, int(os.getenv("AGENT_MAX_RETRIES", "3")))
_BASE_DELAY = max(0.5, float(os.getenv("AGENT_RETRY_BASE_DELAY", "2.0")))


def _parse_retry_after(resp: requests.Response | None) -> float | None:
    """从响应头解析 Retry-After 值（秒数或 HTTP 日期格式），失败返回 None。"""
    if resp is None:
        return None
    val = resp.headers.get("Retry-After")
    if val is None:
        return None
    try:
        return max(0.0, float(val))
    except ValueError:
        from email.utils import parsedate_to_datetime
        try:
            dt = parsedate_to_datetime(val)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            return None


def _calc_backoff(attempt: int, resp: requests.Response | None) -> float:
    """计算退避延迟：优先 Retry-After，否则指数退避 + 抖动，最小 0.5 秒。"""
    retry_after = _parse_retry_after(resp)
    if retry_after is not None:
        return max(retry_after, 0.5)
    return max(_BASE_DELAY * (2 ** (attempt - 1)) * random.uniform(0.5, 1.0), 0.5)


def call_agent(usage: str, prompt: str, max_tokens: int = 1000) -> str:
    """
    usage: 'seed_evolution' 或 'keyword_report'
    从数据库读取对应智能体配置，发起请求，返回文本内容。
    未分配时抛出 ValueError。
    """
    agent = get_assignment(usage)
    if not agent:
        raise ValueError(f"用途 [{usage}] 未分配智能体，请在智能体管理中配置")

    api_key = decrypt_api_key(agent["api_key_enc"])
    base_url = agent["base_url"].rstrip("/")
    auth_type = agent.get("auth_type") or "x_api_key"

    # SSRF 防护：协议校验 + 禁止内网地址
    parsed = urlparse(base_url)
    allow_http = os.getenv("AGENT_ALLOW_HTTP", "").lower() in ("true", "1", "yes")
    if parsed.scheme == "http" and not allow_http:
        raise ValueError(
            f"智能体 [{agent['name']}] base_url 必须使用 https 协议"
            "（本地开发可设置 AGENT_ALLOW_HTTP=true）"
        )
    if parsed.scheme not in ("https", "http"):
        raise ValueError(
            f"智能体 [{agent['name']}] base_url 协议不支持: {parsed.scheme}"
        )
    if _PRIVATE_HOST_RE.match(parsed.hostname or ""):
        raise ValueError(f"智能体 [{agent['name']}] base_url 不允许指向内网地址")

    endpoint = f"{base_url}/v1/messages"
    model = agent["model"]
    version = agent.get("version") or "2023-06-01"

    # 认证头部适配
    if auth_type == "bearer":
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": version,
        }
    else:  # x_api_key (Anthropic 原生)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": version,
        }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    for attempt in range(1, max(1, _MAX_RETRIES + 1)):
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        except requests.exceptions.Timeout as exc:
            if attempt < _MAX_RETRIES:
                delay = _calc_backoff(attempt, resp=None)
                logger.warning(
                    "智能体 [%s] 请求超时（第 %d/%d 次），%.1f 秒后重试",
                    agent["name"], attempt, _MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            raise ValueError(
                f"智能体 [{agent['name']}] 请求超时，已重试 {_MAX_RETRIES} 次仍不可用"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            if attempt < _MAX_RETRIES:
                delay = _calc_backoff(attempt, resp=None)
                logger.warning(
                    "智能体 [%s] 连接失败（第 %d/%d 次），%.1f 秒后重试",
                    agent["name"], attempt, _MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            raise ValueError(
                f"智能体 [{agent['name']}] 连接失败，已重试 {_MAX_RETRIES} 次仍不可用"
            ) from exc

        # 非瞬态错误：即抛，不走重试
        if resp.status_code == 401:
            raise ValueError(f"智能体 [{agent['name']}] API Key 无效（401）")
        if resp.status_code == 403:
            hint = "权限不足或模型不可用" if auth_type == "bearer" else "API Key 权限不足或账户受限"
            raise ValueError(f"智能体 [{agent['name']}] 请求被拒绝（403）：{hint}")
        if resp.status_code == 404:
            raise ValueError(
                f"智能体 [{agent['name']}] 接口地址不存在（404），请检查配置"
            )

        # 422 / 400 / 409：非瞬态但需提取响应体诊断信息
        if resp.status_code == 422:
            try:
                err_body = resp.json().get("error", {})
                err_msg = err_body.get("message", resp.text[:500])
            except Exception:
                err_msg = resp.text[:500]
            raise ValueError(f"智能体 [{agent['name']}] 请求被拒绝（422）：{err_msg}")
        if resp.status_code == 400:
            try:
                err_body = resp.json().get("error", {})
                err_msg = err_body.get("message", resp.text[:500])
            except Exception:
                err_msg = resp.text[:500]
            raise ValueError(f"智能体 [{agent['name']}] 请求无效（400）：{err_msg}")
        if resp.status_code == 409:
            try:
                err_body = resp.json().get("error", {})
                err_msg = err_body.get("message", resp.text[:500])
            except Exception:
                err_msg = resp.text[:500]
            raise ValueError(f"智能体 [{agent['name']}] 请求冲突（409）：{err_msg}")

        # 成功：跳出重试循环
        if resp.status_code == 200:
            break

        # 瞬态错误：429/5xx
        if resp.status_code in _TRANSIENT_STATUS_CODES:
            if attempt < _MAX_RETRIES:
                delay = _calc_backoff(attempt, resp)
                logger.warning(
                    "智能体 [%s] 请求失败 %d（第 %d/%d 次），%.1f 秒后重试 | body: %s",
                    agent["name"], resp.status_code, attempt, _MAX_RETRIES, delay,
                    resp.text[:300],
                )
                time.sleep(delay)
                continue
            logger.error(
                "智能体 [%s] 请求失败 %d，已达最大重试次数 %d",
                agent["name"], resp.status_code, _MAX_RETRIES,
            )
            raise ValueError(
                f"智能体 [{agent['name']}] 请求失败 {resp.status_code}，已重试 {_MAX_RETRIES} 次仍不可用"
            )

        # 其他非200非瞬态状态码：即抛
        raise ValueError(
            f"智能体 [{agent['name']}] 请求失败 {resp.status_code}"
        )

    content = resp.json().get("content", [])
    texts = [b["text"] for b in content if b.get("type") == "text"]
    result = "\n".join(texts).strip()
    if not result:
        raise ValueError(f"智能体 [{agent['name']}] 返回空内容")
    return result
