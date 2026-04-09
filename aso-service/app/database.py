"""
MySQL 8.0 持久化：建表、任务状态、关键词批量写入、查询。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)


def _get_connection() -> pymysql.connections.Connection:
    """创建单次使用的数据库连接（无连接池）。"""
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "aso"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "aso_db"),
        charset="utf8mb4",
        connect_timeout=10,
        cursorclass=DictCursor,
        init_command="SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
    )


def init_db() -> None:
    """创建业务表（若不存在）。"""
    sql_keywords = """
    CREATE TABLE IF NOT EXISTS aso_keywords (
      id                    INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      keyword               VARCHAR(255) NOT NULL,
      seed                  VARCHAR(255),
      country               VARCHAR(10)  DEFAULT 'us',
      autocomplete_rank     INT,
      top_reviews           INT,
      avg_reviews           FLOAT,
      top_current_reviews   INT,
      avg_update_age_months FLOAT,
      concentration         FLOAT,
      seed_coverage         INT,
      trend_gap             FLOAT,
      rank_change           INT,
      opportunity_score     FLOAT,
      blue_ocean_score      INT,
      blue_ocean_flags      VARCHAR(500),
      blue_ocean_label      VARCHAR(20),
      scanned_at            DATETIME     NOT NULL,
      scan_batch_id         VARCHAR(36)  NOT NULL,
      INDEX idx_batch   (scan_batch_id),
      INDEX idx_keyword (keyword),
      INDEX idx_score   (blue_ocean_score DESC),
      INDEX idx_scanned (scanned_at DESC)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_unicode_ci
    """
    sql_jobs = """
    CREATE TABLE IF NOT EXISTS aso_scan_jobs (
      id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      batch_id        VARCHAR(36)  NOT NULL UNIQUE,
      status          ENUM('running','done','failed') DEFAULT 'running',
      total_keywords  INT          DEFAULT 0,
      created_at      DATETIME     NOT NULL,
      finished_at     DATETIME,
      error_msg       TEXT,
      INDEX idx_status     (status),
      INDEX idx_created_at (created_at DESC)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_unicode_ci
    """
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(sql_keywords)
            cur.execute(sql_jobs)
        conn.commit()
    except Exception as exc:
        logger.error("init_db 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def create_running_job(batch_id: str) -> None:
    """插入一条 status=running 的扫描任务记录。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aso_scan_jobs (batch_id, status, total_keywords, created_at)
                VALUES (%s, 'running', 0, NOW())
                """,
                (batch_id,),
            )
        conn.commit()
    except Exception as exc:
        logger.error("create_running_job 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def update_job(
    batch_id: str,
    status: str,
    total: int | None = None,
    error: str | None = None,
) -> None:
    """更新任务状态；status 为 done 或 failed 时写入 finished_at。"""
    set_parts: list[str] = ["status=%s"]
    params: list[Any] = [status]
    if total is not None:
        set_parts.append("total_keywords=%s")
        params.append(total)
    if error is not None:
        set_parts.append("error_msg=%s")
        params.append(error)
    if status in ("done", "failed"):
        set_parts.append("finished_at=NOW()")
    params.append(batch_id)
    sql = f"UPDATE aso_scan_jobs SET {', '.join(set_parts)} WHERE batch_id=%s"

    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
        conn.commit()
    except Exception as exc:
        logger.error("update_job 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def insert_keywords(rows: list[dict], batch_id: str, country: str) -> None:
    """批量插入关键词扫描结果（INSERT IGNORE + executemany）。"""
    if not rows:
        return

    scanned_at = datetime.now(timezone.utc).replace(tzinfo=None)
    sql = """
    INSERT IGNORE INTO aso_keywords (
      keyword, seed, country, autocomplete_rank, top_reviews, avg_reviews,
      top_current_reviews, avg_update_age_months, concentration, seed_coverage,
      trend_gap, rank_change, opportunity_score, blue_ocean_score, blue_ocean_flags,
      blue_ocean_label, scanned_at, scan_batch_id
    ) VALUES (
      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    tuples: list[tuple] = []
    for r in rows:
        flags = (r.get("blue_ocean_flags") or "")[:500]
        tuples.append(
            (
                r["keyword"],
                r.get("seed"),
                country,
                r.get("autocomplete_rank"),
                r.get("top_app_reviews"),
                float(r.get("avg_reviews") or 0),
                r.get("top_current_reviews"),
                float(r.get("avg_update_age_months") or 0),
                float(r.get("concentration") or 0),
                r.get("seed_coverage"),
                float(r.get("trend_gap") or 0),
                r.get("rank_change"),
                float(r.get("opportunity_score") or 0),
                int(r.get("blue_ocean_score") or 0),
                flags,
                (r.get("blue_ocean_label") or "")[:20],
                scanned_at,
                batch_id,
            )
        )

    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.executemany(sql, tuples)
        conn.commit()
    except Exception as exc:
        logger.error("insert_keywords 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_job(batch_id: str) -> dict | None:
    """查询单条扫描任务；不存在返回 None。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM aso_scan_jobs WHERE batch_id = %s",
                (batch_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("get_job 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_top_keywords(
    label: str | None = None,
    limit: int = 50,
    days: int = 7,
) -> list[dict]:
    """
    按时间窗口与可选标签筛选，按 blue_ocean_score 降序返回关键词摘要。
    limit 最大 200。
    """
    limit = min(max(limit, 1), 200)
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            if label is not None:
                cur.execute(
                    """
                    SELECT keyword, blue_ocean_score, blue_ocean_label, blue_ocean_flags,
                           top_reviews, concentration, avg_update_age_months,
                           trend_gap, rank_change, scanned_at
                    FROM aso_keywords
                    WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                      AND blue_ocean_label = %s
                    ORDER BY blue_ocean_score DESC
                    LIMIT %s
                    """,
                    (days, label, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT keyword, blue_ocean_score, blue_ocean_label, blue_ocean_flags,
                           top_reviews, concentration, avg_update_age_months,
                           trend_gap, rank_change, scanned_at
                    FROM aso_keywords
                    WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                    ORDER BY blue_ocean_score DESC
                    LIMIT %s
                    """,
                    (days, limit),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_top_keywords 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()
