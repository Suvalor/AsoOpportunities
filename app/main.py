"""
ASO 蓝海扫描服务 — FastAPI 入口。
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .database import get_user_count, init_db
from .routers import agents, analysis, auth_router, report, scan, seeds

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
app.include_router(report.router)
app.include_router(auth_router.router)
app.include_router(agents.router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时初始化数据库表结构。"""
    # 检查 API_KEY 配置
    api_key = os.getenv("API_KEY", "")
    if not api_key:
        print("=" * 50)
        print("[警告] 环境变量 API_KEY 未设置，所有 API 请求将被拒绝。")
        print("  请在 .env 文件中设置 API_KEY 后重启服务。")
        print("=" * 50)
        logger.warning("API_KEY 未配置")

    # 检查 JWT_SECRET 配置
    jwt_secret = os.getenv("JWT_SECRET", "")
    if not jwt_secret:
        print("=" * 50)
        print("[警告] 环境变量 JWT_SECRET 未设置，JWT 认证将不可用。")
        print("  请在 .env 文件中设置 JWT_SECRET 后重启服务。")
        print("=" * 50)
        logger.warning("JWT_SECRET 未配置")

    init_db()
    logger.info("数据库 init_db 完成")

    # 检查 AGENT_ALLOW_HTTP 安全警告
    if os.getenv("AGENT_ALLOW_HTTP", "").lower() in ("true", "1", "yes"):
        logger.warning(
            "AGENT_ALLOW_HTTP=true：智能体允许使用 http 协议的 base_url，"
            "仅限本地开发使用，生产环境请移除此配置"
        )

    # 注册开关状态日志
    allow_reg = os.getenv("ALLOW_REGISTER", "false").lower() == "true"
    logger.info("ALLOW_REGISTER=%s（%s）", allow_reg, "允许注册" if allow_reg else "禁止注册，零用户时仍放行")

    try:
        if get_user_count() == 0:
            print("=" * 50)
            print("[初始化] 系统尚无用户，请访问以下地址完成注册：")
            print("  http://localhost:8000/static/index.html")
            print("  首个注册用户将自动成为管理员")
            print("=" * 50)
    except Exception:
        pass


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/static/dashboard.html")


@app.get("/health")
def health() -> dict:
    """健康检查（无需鉴权）。"""
    return {"status": "ok"}
