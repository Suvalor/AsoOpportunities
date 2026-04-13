"""
MySQL 8.0 持久化：建表、任务状态、关键词批量写入、查询。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import pymysql
from cryptography.fernet import Fernet
from pymysql import err as pymysql_err
from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)


# ===================== 智能体 API Key 加解密 =====================


def _get_fernet() -> Fernet:
    """从环境变量构造 Fernet；密钥须为 64 个十六进制字符（32 字节）。"""
    key = (os.getenv("AGENT_ENCRYPT_KEY", "") or "").strip()
    if len(key) != 64:
        raise ValueError(
            "环境变量 AGENT_ENCRYPT_KEY 未配置或长度不是 64："
            "须为 64 位十六进制字符串（32 字节），"
            "可在宿主机执行：python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    try:
        raw = bytes.fromhex(key)
    except ValueError as exc:
        raise ValueError(
            "AGENT_ENCRYPT_KEY 含有非十六进制字符，请仅使用 0-9、a-f、A-F"
        ) from exc
    if len(raw) != 32:
        raise ValueError("AGENT_ENCRYPT_KEY 解码后必须为 32 字节")
    b64 = base64.urlsafe_b64encode(raw)
    return Fernet(b64)


def encrypt_api_key(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_api_key(cipher: str) -> str:
    return _get_fernet().decrypt(cipher.encode()).decode()


def bootstrap_default_seeds_if_empty() -> None:
    """
    若 aso_seeds 为空，将 aso_core.config_data.SEEDS 批量写入，
    status=active，source=manual，generation=0。
    """
    from aso_core.config_data import SEEDS as CORE_SEEDS

    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM aso_seeds")
            row = cur.fetchone()
            if row and int(row["c"]) > 0:
                return
            sql = """
            INSERT IGNORE INTO aso_seeds
              (seed, status, source, generation, created_at, updated_at)
            VALUES (%s, 'active', 'manual', 0, NOW(), NOW())
            """
            cur.executemany(sql, [(s,) for s in CORE_SEEDS])
        conn.commit()
        logger.info("已初始化 aso_seeds，共 %d 条默认种子", len(CORE_SEEDS))
    except Exception as exc:
        logger.error("bootstrap_default_seeds_if_empty 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def _add_column_if_not_exists(
    conn: pymysql.connections.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """若列不存在则 ALTER TABLE ADD COLUMN（MySQL 8.0 兼容）。"""
    check_sql = """
        SELECT COUNT(*) AS c FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = %s
        AND COLUMN_NAME = %s
    """
    with conn.cursor() as cur:
        cur.execute(check_sql, (table, column))
        row = cur.fetchone()
        if row and int(row["c"]) == 0:
            cur.execute(
                f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"
            )
    conn.commit()


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


def _get_connection_for_init() -> pymysql.connections.Connection:
    """
    供 init_db 使用：在无法连上 MySQL（如外部库尚未就绪）时按次数退避重试，
    避免容器一启动就因 Connection refused 退出。
    """
    max_retries = max(1, int(os.getenv("MYSQL_INIT_MAX_RETRIES", "30")))
    retry_sleep = max(0.5, float(os.getenv("MYSQL_INIT_RETRY_SLEEP_SEC", "2")))
    last_exc: BaseException | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return _get_connection()
        except pymysql_err.OperationalError as exc:
            last_exc = exc
            code = exc.args[0] if exc.args else None
            # 2003：无法连接服务器（含连接被拒绝）
            if attempt < max_retries and code == 2003:
                logger.warning(
                    "等待 MySQL 就绪：第 %d/%d 次连接失败，%.1f 秒后重试（%s）",
                    attempt,
                    max_retries,
                    retry_sleep,
                    exc,
                )
                time.sleep(retry_sleep)
            else:
                raise
    assert last_exc is not None
    raise last_exc


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
    sql_seeds = """
    CREATE TABLE IF NOT EXISTS aso_seeds (
      id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      seed        VARCHAR(255) NOT NULL,
      status      ENUM('active','pending','pruned') NOT NULL DEFAULT 'pending',
      source      VARCHAR(32)  NOT NULL DEFAULT 'manual',
      generation  INT UNSIGNED NOT NULL DEFAULT 0,
      created_at  DATETIME     NOT NULL,
      updated_at  DATETIME     NOT NULL,
      UNIQUE KEY uk_seed (seed),
      INDEX idx_status (status),
      INDEX idx_generation (generation)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_unicode_ci
    """
    sql_evolution_log = """
    CREATE TABLE IF NOT EXISTS aso_seed_evolution_log (
      id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      batch_id   VARCHAR(36),
      event_type VARCHAR(64) NOT NULL,
      payload    JSON,
      created_at DATETIME    NOT NULL,
      INDEX idx_batch (batch_id),
      INDEX idx_created (created_at DESC),
      INDEX idx_event (event_type)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_unicode_ci
    """
    sql_reports = """
    CREATE TABLE IF NOT EXISTS aso_analysis_reports (
      id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      report_md        LONGTEXT     NOT NULL,
      triggered_by     ENUM('auto_threshold','manual','weekly_full')
                       DEFAULT 'auto_threshold',
      keyword_count    INT          DEFAULT 0,
      new_gold_count   INT          DEFAULT 0,
      score_delta_sum  FLOAT        DEFAULT 0,
      keywords_json    JSON,
      prompt_version   INT UNSIGNED DEFAULT 1,
      created_at       DATETIME     NOT NULL,
      INDEX idx_created (created_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    sql_users = """
    CREATE TABLE IF NOT EXISTS aso_users (
      id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      username      VARCHAR(64)   NOT NULL UNIQUE,
      password_hash VARCHAR(255)  NOT NULL,
      role          ENUM('admin','viewer') DEFAULT 'viewer',
      created_at    DATETIME      NOT NULL,
      last_login_at DATETIME,
      INDEX idx_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    sql_agents = """
    CREATE TABLE IF NOT EXISTS aso_agents (
      id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      name            VARCHAR(100)  NOT NULL,
      base_url        VARCHAR(500)  NOT NULL,
      api_key_enc     TEXT          NOT NULL,
      api_key_preview VARCHAR(20)   NOT NULL,
      model           VARCHAR(100)  NOT NULL,
      version         VARCHAR(50)   DEFAULT '2023-06-01',
      is_active       TINYINT(1)    DEFAULT 1,
      created_at      DATETIME      NOT NULL,
      updated_at      DATETIME      NOT NULL,
      INDEX idx_active (is_active)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    sql_assignments = """
    CREATE TABLE IF NOT EXISTS aso_agent_assignments (
      id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      `usage`       ENUM('seed_evolution','keyword_report') NOT NULL UNIQUE,
      agent_id      INT UNSIGNED NOT NULL,
      updated_at    DATETIME NOT NULL,
      FOREIGN KEY (agent_id) REFERENCES aso_agents(id)
        ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """

    conn = None
    try:
        conn = _get_connection_for_init()
        with conn.cursor() as cur:
            cur.execute(sql_keywords)
            cur.execute(sql_jobs)
            cur.execute(sql_seeds)
            cur.execute(sql_evolution_log)
            cur.execute(sql_reports)
            cur.execute(sql_users)
            cur.execute(sql_agents)
            cur.execute(sql_assignments)
        conn.commit()

        _new_columns = [
            ("gplay_autocomplete_rank", "INT DEFAULT NULL"),
            ("gplay_top_reviews", "INT DEFAULT 0"),
            ("gplay_top_installs", "VARCHAR(50) DEFAULT '0'"),
            ("gplay_top_installs_num", "INT DEFAULT 0"),
            ("gplay_avg_rating", "FLOAT DEFAULT 0"),
            ("cross_platform", "TINYINT(1) DEFAULT 0"),
            ("trends_rising", "TINYINT(1) DEFAULT 0"),
            ("trends_rising_count", "INT DEFAULT 0"),
            ("reddit_post_count", "INT DEFAULT 0"),
            ("reddit_avg_score", "FLOAT DEFAULT 0"),
        ]
        for col_name, col_def in _new_columns:
            _add_column_if_not_exists(conn, "aso_keywords", col_name, col_def)

        bootstrap_default_seeds_if_empty()
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


def insert_keywords(rows: list[dict], batch_id: str, country: str = "us") -> None:
    """
    批量插入关键词扫描结果（INSERT IGNORE + executemany）。
    每条 row 应含 country 字段；缺失时回退为参数 country（兼容旧调用）。
    """
    if not rows:
        return

    scanned_at = datetime.now(timezone.utc).replace(tzinfo=None)
    sql = """
    INSERT IGNORE INTO aso_keywords (
      keyword, seed, country, autocomplete_rank, top_reviews, avg_reviews,
      top_current_reviews, avg_update_age_months, concentration, seed_coverage,
      trend_gap, rank_change, opportunity_score, blue_ocean_score, blue_ocean_flags,
      blue_ocean_label, scanned_at, scan_batch_id,
      gplay_autocomplete_rank, gplay_top_reviews, gplay_top_installs,
      gplay_top_installs_num, gplay_avg_rating, cross_platform,
      trends_rising, trends_rising_count, reddit_post_count, reddit_avg_score
    ) VALUES (
      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    """
    tuples: list[tuple] = []
    for r in rows:
        flags = (r.get("blue_ocean_flags") or "")[:500]
        row_country = (r.get("country") or country or "us").strip().lower()[:10]
        tuples.append(
            (
                r["keyword"],
                r.get("seed"),
                row_country,
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
                r.get("gplay_autocomplete_rank"),
                int(r.get("gplay_top_reviews") or 0),
                str(r.get("gplay_top_installs") or "0")[:50],
                int(r.get("gplay_top_installs_num") or 0),
                float(r.get("gplay_avg_rating") or 0),
                1 if r.get("cross_platform") else 0,
                1 if r.get("trends_rising") else 0,
                int(r.get("trends_rising_count") or 0),
                int(r.get("reddit_post_count") or 0),
                float(r.get("reddit_avg_score") or 0),
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
    countries: list[str] | None = None,
    cross_platform: bool | None = None,
    trends_only: bool | None = None,
) -> list[dict]:
    """
    按时间窗口与可选标签、可选国家列表筛选，按 blue_ocean_score 降序返回关键词摘要。
    countries 为 None 时不过滤国家；传入时仅保留 country IN (...) 的行。
    cross_platform=True 时仅返回双平台词；trends_only=True 时仅返回 Trends 上升词。
    limit 最大 200。
    """
    limit = min(max(limit, 1), 200)
    cc_list: list[str] | None = None
    if countries is not None:
        cc_list = [c.strip().lower()[:10] for c in countries if c and str(c).strip()]
        if not cc_list:
            cc_list = None

    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            where_extra = ""
            params: list[Any] = [days]
            if label is not None:
                where_extra += " AND blue_ocean_label = %s"
                params.append(label)
            if cc_list is not None:
                ph = ",".join(["%s"] * len(cc_list))
                where_extra += f" AND country IN ({ph})"
                params.extend(cc_list)
            if cross_platform is True:
                where_extra += " AND cross_platform = 1"
            if trends_only is True:
                where_extra += " AND trends_rising = 1"
            params.append(limit)

            sql = f"""
                    SELECT keyword, country, blue_ocean_score, blue_ocean_label, blue_ocean_flags,
                           top_reviews, concentration, avg_update_age_months,
                           trend_gap, rank_change, scanned_at,
                           gplay_autocomplete_rank, gplay_top_reviews, gplay_top_installs,
                           gplay_top_installs_num, gplay_avg_rating, cross_platform,
                           trends_rising, trends_rising_count,
                           reddit_post_count, reddit_avg_score
                    FROM aso_keywords
                    WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                      {where_extra}
                    ORDER BY blue_ocean_score DESC
                    LIMIT %s
                    """
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_top_keywords 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_active_seeds() -> list[str]:
    """返回 status=active 的种子短语列表（有序）。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT seed FROM aso_seeds
                WHERE status = 'active'
                ORDER BY seed
                """
            )
            return [str(r["seed"]) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_active_seeds 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_tracking_scan_seeds(min_score: int, days: int = 30) -> list[str]:
    """
    追踪扫描用：active 种子中，在最近 days 天内 aso_keywords 曾出现
    blue_ocean_score >= min_score 的 seed（去重）。
    """
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.seed
                FROM aso_seeds s
                WHERE s.status = 'active'
                  AND s.seed IN (
                    SELECT DISTINCT k.seed
                    FROM aso_keywords k
                    WHERE k.blue_ocean_score >= %s
                      AND k.scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                      AND k.seed IS NOT NULL
                      AND k.seed != ''
                  )
                ORDER BY s.seed
                """,
                (min_score, days),
            )
            return [str(r["seed"]) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_tracking_scan_seeds 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def append_evolution_log(
    batch_id: str | None,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """进化日志只追加。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aso_seed_evolution_log (batch_id, event_type, payload, created_at)
                VALUES (%s, %s, %s, NOW())
                """,
                (
                    batch_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.error("append_evolution_log 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_seed_performance_by_batch(batch_id: str) -> list[dict]:
    """按 seed 聚合本批次关键词表现。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  seed,
                  COUNT(*) AS keyword_count,
                  AVG(blue_ocean_score) AS avg_blue_ocean_score,
                  MAX(blue_ocean_score) AS max_blue_ocean_score,
                  SUM(CASE WHEN blue_ocean_score >= 60 THEN 1 ELSE 0 END) AS strong_count,
                  AVG(CASE WHEN cross_platform = 1 THEN 1.0 ELSE 0 END) AS cross_platform_ratio,
                  AVG(CASE WHEN trends_rising = 1 THEN 1.0 ELSE 0 END) AS trends_ratio
                FROM aso_keywords
                WHERE scan_batch_id = %s
                  AND seed IS NOT NULL AND seed != ''
                GROUP BY seed
                """,
                (batch_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_seed_performance_by_batch 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def set_seeds_pruned(seed_list: list[str]) -> None:
    """将给定 seed 标记为 pruned。"""
    if not seed_list:
        return
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(seed_list))
            cur.execute(
                f"""
                UPDATE aso_seeds
                SET status = 'pruned', updated_at = NOW()
                WHERE seed IN ({placeholders}) AND status = 'active'
                """,
                tuple(seed_list),
            )
        conn.commit()
    except Exception as exc:
        logger.error("set_seeds_pruned 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def insert_pending_seeds(seeds: list[str], generation: int, source: str = "generated") -> None:
    """插入待验证种子（pending）；已存在则忽略。"""
    if not seeds:
        return
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            sql = """
            INSERT IGNORE INTO aso_seeds
              (seed, status, source, generation, created_at, updated_at)
            VALUES (%s, 'pending', %s, %s, NOW(), NOW())
            """
            cur.executemany(
                sql,
                [(s.strip(), source, generation) for s in seeds if s.strip()],
            )
        conn.commit()
    except Exception as exc:
        logger.error("insert_pending_seeds 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def fetch_pending_seeds_ordered(limit: int = 100) -> list[str]:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT seed FROM aso_seeds
                WHERE status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [str(r["seed"]) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("fetch_pending_seeds_ordered 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def activate_seed(seed: str) -> bool:
    """将 pending 转为 active；返回是否更新成功。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE aso_seeds
                SET status = 'active', updated_at = NOW()
                WHERE seed = %s AND status = 'pending'
                """,
                (seed,),
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    except Exception as exc:
        logger.error("activate_seed 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def max_seed_generation() -> int:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(generation), 0) AS m FROM aso_seeds")
            row = cur.fetchone()
            return int(row["m"] or 0)
    except Exception as exc:
        logger.error("max_seed_generation 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_top_keywords_for_batch(batch_id: str, limit: int) -> list[dict]:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT keyword, blue_ocean_score, seed, blue_ocean_flags,
                       cross_platform, trends_rising
                FROM aso_keywords
                WHERE scan_batch_id = %s
                ORDER BY blue_ocean_score DESC
                LIMIT %s
                """,
                (batch_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_top_keywords_for_batch 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_seeds_status_snapshot() -> dict:
    """供 GET /seeds/status 的快照数据。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*) AS c FROM aso_seeds GROUP BY status
                """
            )
            counts = {str(r["status"]): int(r["c"]) for r in cur.fetchall()}
            cur.execute("SELECT COALESCE(MAX(generation), 0) AS m FROM aso_seeds")
            max_gen = int(cur.fetchone()["m"] or 0)
            cur.execute(
                """
                SELECT seed, status, source, generation, created_at, updated_at
                FROM aso_seeds
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 30
                """
            )
            pending_preview = []
            for r in cur.fetchall():
                pending_preview.append(
                    {
                        "seed": r["seed"],
                        "status": r["status"],
                        "source": r["source"],
                        "generation": int(r["generation"] or 0),
                        "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                        if hasattr(r["created_at"], "strftime")
                        else str(r["created_at"]),
                        "updated_at": r["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
                        if hasattr(r["updated_at"], "strftime")
                        else str(r["updated_at"]),
                    }
                )
            cur.execute(
                """
                SELECT id, batch_id, event_type, payload, created_at
                FROM aso_seed_evolution_log
                ORDER BY id DESC
                LIMIT 25
                """
            )
            recent_events = []
            for r in cur.fetchall():
                pl = r.get("payload")
                if isinstance(pl, str):
                    try:
                        pl = json.loads(pl)
                    except json.JSONDecodeError:
                        pass
                recent_events.append(
                    {
                        "id": int(r["id"]),
                        "batch_id": r.get("batch_id"),
                        "event_type": r["event_type"],
                        "payload": pl,
                        "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                        if hasattr(r["created_at"], "strftime")
                        else str(r["created_at"]),
                    }
                )
        return {
            "active_count": int(counts.get("active", 0)),
            "pending_count": int(counts.get("pending", 0)),
            "pruned_count": int(counts.get("pruned", 0)),
            "max_generation": max_gen,
            "pending_preview": pending_preview,
            "recent_evolution_events": recent_events,
        }
    except Exception as exc:
        logger.error("get_seeds_status_snapshot 失败: %s", exc)
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
                         trend_gap, rank_change, scanned_at,
                         gplay_autocomplete_rank, gplay_top_reviews, gplay_top_installs,
                         gplay_top_installs_num, gplay_avg_rating, cross_platform,
                         trends_rising, trends_rising_count,
                         reddit_post_count, reddit_avg_score
                  FROM aso_keywords
                  WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                ),
                recent_latest AS (
                  SELECT keyword, blue_ocean_score, blue_ocean_label, blue_ocean_flags,
                         top_reviews, concentration, avg_update_age_months,
                         trend_gap, rank_change, scanned_at,
                         gplay_autocomplete_rank, gplay_top_reviews, gplay_top_installs,
                         gplay_top_installs_num, gplay_avg_rating, cross_platform,
                         trends_rising, trends_rising_count,
                         reddit_post_count, reddit_avg_score
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
                  rl.gplay_autocomplete_rank,
                  rl.gplay_top_reviews,
                  rl.gplay_top_installs,
                  rl.gplay_top_installs_num,
                  rl.gplay_avg_rating,
                  rl.cross_platform,
                  rl.trends_rising,
                  rl.trends_rising_count,
                  rl.reddit_post_count,
                  rl.reddit_avg_score,
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
                "gplay_autocomplete_rank": r.get("gplay_autocomplete_rank"),
                "gplay_top_reviews": r.get("gplay_top_reviews"),
                "gplay_top_installs": r.get("gplay_top_installs"),
                "gplay_top_installs_num": r.get("gplay_top_installs_num"),
                "gplay_avg_rating": r.get("gplay_avg_rating"),
                "cross_platform": r.get("cross_platform"),
                "trends_rising": r.get("trends_rising"),
                "trends_rising_count": r.get("trends_rising_count"),
                "reddit_post_count": r.get("reddit_post_count"),
                "reddit_avg_score": r.get("reddit_avg_score"),
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
        "gplay_autocomplete_rank": row.get("gplay_autocomplete_rank"),
        "gplay_top_reviews": int(row.get("gplay_top_reviews") or 0),
        "gplay_top_installs": str(row.get("gplay_top_installs") or "0"),
        "gplay_top_installs_num": int(row.get("gplay_top_installs_num") or 0),
        "gplay_avg_rating": float(row.get("gplay_avg_rating") or 0),
        "cross_platform": bool(row.get("cross_platform")),
        "trends_rising": bool(row.get("trends_rising")),
        "trends_rising_count": int(row.get("trends_rising_count") or 0),
        "reddit_post_count": int(row.get("reddit_post_count") or 0),
        "reddit_avg_score": float(row.get("reddit_avg_score") or 0),
    }


# ===================== 报告相关查询 =====================


def get_latest_report() -> dict | None:
    """返回最新一份报告（不含 keywords_json）；不存在时返回 None。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, report_md, triggered_by, keyword_count,
                       new_gold_count, score_delta_sum, prompt_version, created_at
                FROM aso_analysis_reports
                ORDER BY id DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("get_latest_report 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_report_history(limit: int = 20) -> list[dict]:
    """返回历史报告列表（不含 report_md 全文）。"""
    limit = min(max(limit, 1), 50)
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, triggered_by, keyword_count, new_gold_count,
                       score_delta_sum, prompt_version, created_at
                FROM aso_analysis_reports
                ORDER BY id DESC LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_report_history 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_report_by_id(report_id: int) -> dict | None:
    """按 id 返回报告全文；不存在返回 None。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM aso_analysis_reports WHERE id = %s",
                (report_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("get_report_by_id 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def insert_report(data: dict) -> int:
    """插入一条报告，返回新记录 id。"""
    kw_json = data.get("keywords_json")
    if isinstance(kw_json, (list, dict)):
        kw_json = json.dumps(kw_json, ensure_ascii=False)
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aso_analysis_reports
                  (report_md, triggered_by, keyword_count, new_gold_count,
                   score_delta_sum, keywords_json, prompt_version, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    data["report_md"],
                    data.get("triggered_by", "auto_threshold"),
                    int(data.get("keyword_count", 0)),
                    int(data.get("new_gold_count", 0)),
                    float(data.get("score_delta_sum", 0)),
                    kw_json,
                    int(data.get("prompt_version", 1)),
                    data.get("created_at", datetime.now(timezone.utc).replace(tzinfo=None)),
                ),
            )
            new_id = cur.lastrowid
        conn.commit()
        return new_id
    except Exception as exc:
        logger.error("insert_report 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_keyword_snapshot_for_report() -> tuple[list[dict], list[str]]:
    """
    返回 (top_keywords, new_keywords)：
    - top_keywords: 最近14天 blue_ocean_score >= 60 的词，按 peak_score 降序，最多100条
    - new_keywords: 7天内首次出现的高分词关键词名列表
    """
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    keyword,
                    MAX(blue_ocean_score)      AS peak_score,
                    MAX(blue_ocean_label)      AS label,
                    MAX(blue_ocean_flags)      AS flags,
                    MAX(top_reviews)           AS top_reviews,
                    MAX(concentration)         AS concentration,
                    MAX(avg_update_age_months) AS avg_update_age_months,
                    MAX(trend_gap)             AS trend_gap,
                    MAX(rank_change)           AS rank_change,
                    MAX(cross_platform)        AS cross_platform,
                    MAX(trends_rising)         AS trends_rising,
                    COUNT(DISTINCT DATE(scanned_at)) AS days_seen,
                    MIN(scanned_at)            AS first_seen,
                    MAX(scanned_at)            AS last_seen
                FROM aso_keywords
                WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
                  AND blue_ocean_score >= 60
                GROUP BY keyword
                ORDER BY peak_score DESC
                LIMIT 100
                """
            )
            top_keywords = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT DISTINCT keyword FROM aso_keywords
                WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                  AND blue_ocean_score >= 60
                  AND keyword NOT IN (
                      SELECT DISTINCT keyword FROM aso_keywords
                      WHERE scanned_at < DATE_SUB(NOW(), INTERVAL 7 DAY)
                  )
                """
            )
            new_keywords = [str(r["keyword"]) for r in cur.fetchall()]

            return top_keywords, new_keywords
    except Exception as exc:
        logger.error("get_keyword_snapshot_for_report 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_recent_score_delta_sum(days: int = 7) -> float:
    """近 N 天内各关键词 blue_ocean_score 变化绝对值之和。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(ABS(score_delta)), 0) AS total_delta
                FROM (
                    SELECT
                        keyword,
                        blue_ocean_score - LAG(blue_ocean_score)
                            OVER (PARTITION BY keyword ORDER BY scanned_at) AS score_delta
                    FROM aso_keywords
                    WHERE scanned_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                ) t
                WHERE score_delta IS NOT NULL
                """,
                (days,),
            )
            row = cur.fetchone()
            return float(row["total_delta"]) if row else 0.0
    except Exception as exc:
        logger.error("get_recent_score_delta_sum 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


# ===================== 用户相关 =====================


def create_user(username: str, password_hash: str, role: str = "viewer") -> int:
    """插入新用户，返回 id。username 重复时由调用方捕获 IntegrityError。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aso_users (username, password_hash, role, created_at)
                VALUES (%s, %s, %s, NOW())
                """,
                (username, password_hash, role),
            )
            new_id = cur.lastrowid
        conn.commit()
        return new_id
    except Exception as exc:
        logger.error("create_user 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM aso_users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("get_user_by_username 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_user_count() -> int:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM aso_users")
            row = cur.fetchone()
            return int(row["c"]) if row else 0
    except Exception as exc:
        logger.error("get_user_count 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def update_last_login(user_id: int) -> None:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE aso_users SET last_login_at = NOW() WHERE id = %s",
                (user_id,),
            )
        conn.commit()
    except Exception as exc:
        logger.error("update_last_login 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


# ===================== 智能体相关 =====================


def get_all_agents() -> list[dict]:
    """列出所有智能体（不含 api_key_enc）。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, base_url, api_key_preview, model, version,
                       is_active, created_at, updated_at
                FROM aso_agents ORDER BY id ASC
                """
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_all_agents 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_agent_by_id(agent_id: int) -> dict | None:
    """按 id 返回智能体（含 api_key_enc，仅供内部调用）。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM aso_agents WHERE id = %s",
                (agent_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("get_agent_by_id 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def insert_agent(data: dict) -> int:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aso_agents
                  (name, base_url, api_key_enc, api_key_preview, model, version,
                   is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    data["name"],
                    data["base_url"],
                    data["api_key_enc"],
                    data["api_key_preview"],
                    data["model"],
                    data.get("version", "2023-06-01"),
                    1 if data.get("is_active", True) else 0,
                ),
            )
            new_id = cur.lastrowid
        conn.commit()
        return new_id
    except Exception as exc:
        logger.error("insert_agent 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def update_agent(agent_id: int, data: dict) -> None:
    """更新智能体，data 中不含 api_key_enc 时不更新密钥。"""
    set_parts: list[str] = []
    params: list[Any] = []
    for field in ("name", "base_url", "model", "version"):
        if field in data:
            set_parts.append(f"{field}=%s")
            params.append(data[field])
    if "api_key_enc" in data:
        set_parts.append("api_key_enc=%s")
        params.append(data["api_key_enc"])
        set_parts.append("api_key_preview=%s")
        params.append(data["api_key_preview"])
    if "is_active" in data:
        set_parts.append("is_active=%s")
        params.append(1 if data["is_active"] else 0)
    set_parts.append("updated_at=NOW()")
    if not set_parts:
        return
    params.append(agent_id)
    sql = f"UPDATE aso_agents SET {', '.join(set_parts)} WHERE id=%s"
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
        conn.commit()
    except Exception as exc:
        logger.error("update_agent 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def delete_agent(agent_id: int) -> None:
    """删除智能体。若被 aso_agent_assignments 引用则抛出 ValueError。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM aso_agent_assignments WHERE agent_id=%s",
                (agent_id,),
            )
            row = cur.fetchone()
            if row and int(row["c"]) > 0:
                raise ValueError("该智能体已被用途分配引用，请先解除分配再删除")
            cur.execute("DELETE FROM aso_agents WHERE id=%s", (agent_id,))
        conn.commit()
    except ValueError:
        raise
    except Exception as exc:
        logger.error("delete_agent 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


# ===================== 用途分配 =====================


def get_assignment(usage: str) -> dict | None:
    """查询指定用途对应的智能体配置（含 api_key_enc）。"""
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.* FROM aso_agent_assignments aa
                JOIN aso_agents a ON a.id = aa.agent_id
                WHERE aa.`usage` = %s
                """,
                (usage,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("get_assignment 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def set_assignment(usage: str, agent_id: int) -> None:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aso_agent_assignments (`usage`, agent_id, updated_at)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE agent_id=%s, updated_at=NOW()
                """,
                (usage, agent_id, agent_id),
            )
        conn.commit()
    except Exception as exc:
        logger.error("set_assignment 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def get_all_assignments() -> list[dict]:
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT aa.`usage`, aa.agent_id, a.name AS agent_name,
                       a.model, a.is_active, aa.updated_at
                FROM aso_agent_assignments aa
                JOIN aso_agents a ON a.id = aa.agent_id
                ORDER BY aa.`usage`
                """
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_all_assignments 失败: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()
