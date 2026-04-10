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


def get_tracked_keywords(min_score: int) -> list[str]:
    """
    返回「每个关键词最新一条扫描记录中蓝海分 >= min_score」的去重关键词列表（按 keyword 排序）。
    使用 ROW_NUMBER 按 keyword 分区、按 scanned_at 降序取最新一行。
    """
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH latest AS (
                  SELECT keyword,
                         blue_ocean_score,
                         ROW_NUMBER() OVER (
                           PARTITION BY keyword ORDER BY scanned_at DESC
                         ) AS rn
                  FROM aso_keywords
                )
                SELECT keyword
                FROM latest
                WHERE rn = 1 AND blue_ocean_score >= %s
                ORDER BY keyword
                """,
                (min_score,),
            )
            rows = cur.fetchall()
            return [str(r["keyword"]) for r in rows]
    except Exception as exc:
        logger.error("get_tracked_keywords 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_compare_analysis(
    days_recent: int = 7,
    days_baseline: int = 14,
) -> dict[str, list[dict]]:
    """
    本期窗口：最近 days_recent 天；基线窗口：其前连续 days_baseline 天。
    对每期按 keyword 取最新一条快照，用 UNION + LAG() 得到基线分，再算 score_delta。

    分类（仅包含「本期窗口内出现过」的关键词）：
    - new_entries：基线窗口无该词，score_delta = 本期分数
    - rising / dropping / sustained：两期均有，按 score_delta 符号或零划分
    """
    days_recent = max(1, int(days_recent))
    days_baseline = max(1, int(days_baseline))

    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH baseline_scoped AS (
                  SELECT keyword, blue_ocean_score, scanned_at
                  FROM aso_keywords
                  WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                    AND scanned_at < DATE_SUB(NOW(), INTERVAL %s DAY)
                ),
                baseline_latest AS (
                  SELECT keyword, blue_ocean_score, scanned_at
                  FROM (
                    SELECT *, ROW_NUMBER() OVER (
                      PARTITION BY keyword ORDER BY scanned_at DESC
                    ) AS rn
                    FROM baseline_scoped
                  ) t
                  WHERE rn = 1
                ),
                recent_scoped AS (
                  SELECT keyword, blue_ocean_score, blue_ocean_label, blue_ocean_flags,
                         top_reviews, concentration, avg_update_age_months,
                         trend_gap, rank_change, scanned_at
                  FROM aso_keywords
                  WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                ),
                recent_latest AS (
                  SELECT keyword, blue_ocean_score, blue_ocean_label, blue_ocean_flags,
                         top_reviews, concentration, avg_update_age_months,
                         trend_gap, rank_change, scanned_at
                  FROM (
                    SELECT *, ROW_NUMBER() OVER (
                      PARTITION BY keyword ORDER BY scanned_at DESC
                    ) AS rn
                    FROM recent_scoped
                  ) t
                  WHERE rn = 1
                ),
                union_scores AS (
                  SELECT keyword, blue_ocean_score, 0 AS phase
                  FROM baseline_latest
                  UNION ALL
                  SELECT keyword, blue_ocean_score, 1 AS phase
                  FROM recent_latest
                ),
                lagged AS (
                  SELECT keyword, phase, blue_ocean_score,
                         LAG(blue_ocean_score) OVER (
                           PARTITION BY keyword ORDER BY phase
                         ) AS lag_baseline_score
                  FROM union_scores
                )
                SELECT
                  rl.keyword,
                  rl.blue_ocean_score,
                  rl.blue_ocean_label,
                  rl.blue_ocean_flags,
                  rl.top_reviews,
                  rl.concentration,
                  rl.avg_update_age_months,
                  rl.trend_gap,
                  rl.rank_change,
                  rl.scanned_at,
                  lg.lag_baseline_score,
                  CASE
                    WHEN lg.lag_baseline_score IS NULL THEN rl.blue_ocean_score
                    ELSE rl.blue_ocean_score - lg.lag_baseline_score
                  END AS score_delta
                FROM recent_latest rl
                INNER JOIN lagged lg
                  ON rl.keyword = lg.keyword AND lg.phase = 1
                """,
                (
                    days_recent + days_baseline,
                    days_recent,
                    days_recent,
                ),
            )
            rows = [dict(r) for r in cur.fetchall()]

        rising: list[dict] = []
        new_entries: list[dict] = []
        sustained: list[dict] = []
        dropping: list[dict] = []

        for r in rows:
            delta = int(r["score_delta"] or 0)
            lag_bs = r.get("lag_baseline_score")
            base_row = {
                "keyword": r["keyword"],
                "blue_ocean_score": r["blue_ocean_score"],
                "blue_ocean_label": r.get("blue_ocean_label"),
                "blue_ocean_flags": r.get("blue_ocean_flags"),
                "top_reviews": r.get("top_reviews"),
                "concentration": r.get("concentration"),
                "avg_update_age_months": r.get("avg_update_age_months"),
                "trend_gap": r.get("trend_gap"),
                "rank_change": r.get("rank_change"),
                "scanned_at": r.get("scanned_at"),
            }
            item = _compare_row_dict(base_row, delta)
            if lag_bs is None:
                new_entries.append(item)
            elif delta > 0:
                rising.append(item)
            elif delta < 0:
                dropping.append(item)
            else:
                sustained.append(item)

        return {
            "rising": rising,
            "new_entries": new_entries,
            "sustained": sustained,
            "dropping": dropping,
        }
    except Exception as exc:
        logger.error("get_compare_analysis 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def _compare_row_dict(row: dict, score_delta: int) -> dict:
    """与 GET /analysis/top 中单条 keyword 对象一致，并附加 score_delta。"""
    return {
        "keyword": row["keyword"],
        "blue_ocean_score": int(row["blue_ocean_score"] or 0),
        "blue_ocean_label": row.get("blue_ocean_label") or "",
        "blue_ocean_flags": row.get("blue_ocean_flags") or "",
        "top_reviews": row.get("top_reviews"),
        "concentration": float(row["concentration"])
        if row.get("concentration") is not None
        else None,
        "avg_update_age_months": float(row["avg_update_age_months"])
        if row.get("avg_update_age_months") is not None
        else None,
        "trend_gap": float(row["trend_gap"])
        if row.get("trend_gap") is not None
        else None,
        "rank_change": row.get("rank_change"),
        "scanned_at": row["scanned_at"].strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(row.get("scanned_at"), "strftime")
        else str(row.get("scanned_at") or ""),
        "score_delta": score_delta,
    }
