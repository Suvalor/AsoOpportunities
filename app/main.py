"""
ASO 蓝海扫描服务 — FastAPI 入口。
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI

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


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时初始化数据库表结构。"""
    init_db()
    logger.info("数据库 init_db 完成")


@app.get("/health")
def health() -> dict:
    """健康检查（无需鉴权）。"""
    return {"status": "ok"}
