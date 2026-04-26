"""
统一智能体调用入口：从数据库读取配置，发起 LLM API 请求。
支持 Anthropic 原生协议 (x-api-key) 和兼容协议 (Bearer Token)。
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

import requests

from .database import decrypt_api_key, get_assignment

logger = logging.getLogger(__name__)

# base_url 安全校验：禁止内网地址
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

    resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)

    if resp.status_code == 401:
        raise ValueError(f"智能体 [{agent['name']}] API Key 无效（401）")
    if resp.status_code == 403:
        hint = "权限不足或模型不可用" if auth_type == "bearer" else "API Key 权限不足或账户受限"
        raise ValueError(f"智能体 [{agent['name']}] 请求被拒绝（403）：{hint}")
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
