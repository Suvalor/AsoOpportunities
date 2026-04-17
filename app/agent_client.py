"""
统一智能体调用入口：从数据库读取配置，发起 Anthropic API 请求。
替代原先在 evolution.py / report_engine.py 中硬编码的 Anthropic 调用。
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import requests

from .database import decrypt_api_key, get_assignment

logger = logging.getLogger(__name__)

# base_url 安全校验：仅允许 https 协议且禁止内网地址
_PRIVATE_HOST_RE = re.compile(
    r"^(127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.|localhost|::1|fe80:)",
    re.IGNORECASE,
)


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

    # SSRF 防护：仅允许 https，禁止内网地址
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise ValueError(f"智能体 [{agent['name']}] base_url 必须使用 https 协议")
    if _PRIVATE_HOST_RE.match(parsed.hostname or ""):
        raise ValueError(f"智能体 [{agent['name']}] base_url 不允许指向内网地址")

    endpoint = f"{base_url}/v1/messages"
    model = agent["model"]
    version = agent.get("version") or "2023-06-01"

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

    resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)

    if resp.status_code == 401:
        raise ValueError(f"智能体 [{agent['name']}] API Key 无效（401）")
    if resp.status_code == 404:
        raise ValueError(
            f"智能体 [{agent['name']}] 接口地址不存在（404），请检查配置"
        )
    if resp.status_code != 200:
        raise ValueError(
            f"智能体 [{agent['name']}] 请求失败 {resp.status_code}"
        )

    content = resp.json().get("content", [])
    texts = [b["text"] for b in content if b.get("type") == "text"]
    result = "\n".join(texts).strip()
    if not result:
        raise ValueError(f"智能体 [{agent['name']}] 返回空内容")
    return result
