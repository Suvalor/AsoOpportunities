"""
ASO 蓝海扫描服务 — FastAPI 入口。
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routers import analysis, scan, seeds

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASO 蓝海关键词服务", version="1.0.0")

app.include_router(scan.router)
app.include_router(analysis.router)
app.include_router(seeds.router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时初始化数据库表结构。"""
    init_db()
    logger.info("数据库 init_db 完成")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    masked = (
        (api_key[:8] + "..." + api_key[-4:]) if len(api_key) > 12 else "未设置"
    )
    anthropic_base = os.getenv(
        "ANTHROPIC_BASE_URL", "https://api.anthropic.com"
    ).rstrip("/")
    anthropic_endpoint = f"{anthropic_base}/v1/messages"
    print(f"[Anthropic] endpoint : {anthropic_endpoint}")
    print(
        f"[Anthropic] model    : {os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')}"
    )
    print(f"[Anthropic] api_key  : {masked}")


@app.get("/health")
def health() -> dict:
    """健康检查（无需鉴权）。"""
    return {"status": "ok"}
