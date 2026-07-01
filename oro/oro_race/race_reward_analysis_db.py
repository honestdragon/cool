#!/usr/bin/env python3
"""SQLite storage for race query reward analysis (correct vs failed products)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "race_reward_analysis.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Path = DEFAULT_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reward_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_number INTEGER NOT NULL,
                category TEXT,
                query_code TEXT,
                query TEXT NOT NULL,
                correct_product_ids TEXT NOT NULL,
                correct_reward_json TEXT,
                correct_product_info_json TEXT,
                failed_products_json TEXT NOT NULL DEFAULT '[]',
                failed_product_info_json TEXT NOT NULL DEFAULT '{}',
                analysis_notes TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(race_number, query)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS failed_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_number INTEGER,
                query TEXT NOT NULL,
                query_code TEXT,
                category TEXT,
                agent_name TEXT,
                recommended_product_ids TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_failed_attempts_query ON failed_attempts(query)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reward_analysis_race ON reward_analysis(race_number)"
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key in (
        "correct_reward_json",
        "correct_product_info_json",
        "failed_products_json",
        "failed_product_info_json",
    ):
        raw = item.get(key)
        if raw:
            try:
                item[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                item[key.replace("_json", "")] = None
        else:
            item[key.replace("_json", "")] = None
    return item


def upsert_analysis(record: dict, db_path: Path = DEFAULT_DB) -> int:
    init_db(db_path)
    updated_at = record.get("updated_at") or _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reward_analysis (
                race_number, category, query_code, query,
                correct_product_ids, correct_reward_json,
                correct_product_info_json, failed_products_json,
                failed_product_info_json, analysis_notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_number, query) DO UPDATE SET
                category = COALESCE(excluded.category, reward_analysis.category),
                query_code = COALESCE(excluded.query_code, reward_analysis.query_code),
                correct_product_ids = COALESCE(excluded.correct_product_ids, reward_analysis.correct_product_ids),
                correct_reward_json = COALESCE(excluded.correct_reward_json, reward_analysis.correct_reward_json),
                correct_product_info_json = COALESCE(excluded.correct_product_info_json, reward_analysis.correct_product_info_json),
                failed_products_json = COALESCE(excluded.failed_products_json, reward_analysis.failed_products_json),
                failed_product_info_json = COALESCE(excluded.failed_product_info_json, reward_analysis.failed_product_info_json),
                analysis_notes = COALESCE(excluded.analysis_notes, reward_analysis.analysis_notes),
                updated_at = excluded.updated_at
            """,
            (
                int(record["race_number"]),
                record.get("category"),
                record.get("query_code"),
                str(record["query"]),
                str(record["correct_product_ids"]),
                _dump_json(record.get("correct_reward_json")),
                _dump_json(record.get("correct_product_info_json")),
                _dump_json(record.get("failed_products_json") or []),
                _dump_json(record.get("failed_product_info_json") or {}),
                record.get("analysis_notes"),
                updated_at,
            ),
        )
        row = conn.execute(
            "SELECT id FROM reward_analysis WHERE race_number = ? AND query = ?",
            (int(record["race_number"]), str(record["query"])),
        ).fetchone()
        conn.commit()
    return int(row["id"]) if row else 0


def _dump_json(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def add_failed_attempt(record: dict, db_path: Path = DEFAULT_DB) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO failed_attempts (
                race_number, query, query_code, category,
                agent_name, recommended_product_ids, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("race_number"),
                str(record["query"]),
                record.get("query_code"),
                record.get("category"),
                record.get("agent_name"),
                str(record["recommended_product_ids"]),
                str(record.get("source") or "unknown"),
                record.get("created_at") or _now(),
            ),
        )
        conn.commit()


def clear_failed_attempts(db_path: Path = DEFAULT_DB, *, source: str | None = None) -> int:
    init_db(db_path)
    with _connect(db_path) as conn:
        if source:
            cur = conn.execute("DELETE FROM failed_attempts WHERE source = ?", (source,))
        else:
            cur = conn.execute("DELETE FROM failed_attempts")
        conn.commit()
        return cur.rowcount


def list_failed_attempts(db_path: Path = DEFAULT_DB) -> list[dict]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM failed_attempts ORDER BY id ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def list_analysis(
    db_path: Path = DEFAULT_DB,
    *,
    race_number: int | None = None,
    category: str | None = None,
    has_failures: bool | None = None,
    search: str | None = None,
) -> list[dict]:
    init_db(db_path)
    clauses: list[str] = []
    params: list[object] = []
    if race_number is not None:
        clauses.append("race_number = ?")
        params.append(int(race_number))
    if category:
        clauses.append("category = ?")
        params.append(category)
    if has_failures is True:
        clauses.append("failed_products_json != '[]'")
    elif has_failures is False:
        clauses.append("failed_products_json = '[]'")
    if search:
        clauses.append(
            "(query LIKE ? OR correct_product_ids LIKE ? OR query_code LIKE ? OR analysis_notes LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, race_number, category, query_code, query,
                   correct_product_ids, failed_products_json, analysis_notes, updated_at
            FROM reward_analysis
            {where}
            ORDER BY race_number ASC, id ASC
            """,
            params,
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        failed = json.loads(item.get("failed_products_json") or "[]")
        item["failed_count"] = len(failed)
        item["failed_top_ids"] = ", ".join(
            entry.get("product_ids", "") for entry in failed[:3]
        )
        del item["failed_products_json"]
        results.append(item)
    return results


def get_analysis(analysis_id: int, db_path: Path = DEFAULT_DB) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM reward_analysis WHERE id = ?",
            (int(analysis_id),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_analysis_notes(analysis_id: int, notes: str, db_path: Path = DEFAULT_DB) -> bool:
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE reward_analysis SET analysis_notes = ?, updated_at = ? WHERE id = ?",
            (notes, _now(), int(analysis_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def update_failed_products(
    analysis_id: int,
    failed_products: list,
    failed_product_info: dict | None = None,
    db_path: Path = DEFAULT_DB,
) -> bool:
    init_db(db_path)
    with _connect(db_path) as conn:
        if failed_product_info is not None:
            cur = conn.execute(
                """
                UPDATE reward_analysis
                SET failed_products_json = ?, failed_product_info_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(failed_products, ensure_ascii=False),
                    json.dumps(failed_product_info, ensure_ascii=False),
                    _now(),
                    int(analysis_id),
                ),
            )
        else:
            cur = conn.execute(
                """
                UPDATE reward_analysis
                SET failed_products_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(failed_products, ensure_ascii=False),
                    _now(),
                    int(analysis_id),
                ),
            )
        conn.commit()
        return cur.rowcount > 0


def get_summary(db_path: Path = DEFAULT_DB) -> dict:
    init_db(db_path)
    with _connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM reward_analysis").fetchone()["c"]
        with_failures = conn.execute(
            "SELECT COUNT(*) AS c FROM reward_analysis WHERE failed_products_json != '[]'"
        ).fetchone()["c"]
        with_product_info = conn.execute(
            """
            SELECT COUNT(*) AS c FROM reward_analysis
            WHERE correct_product_info_json IS NOT NULL
              AND correct_product_info_json != ''
              AND correct_product_info_json != 'null'
            """
        ).fetchone()["c"]
        races = conn.execute(
            "SELECT COUNT(DISTINCT race_number) AS c FROM reward_analysis"
        ).fetchone()["c"]
        attempts = conn.execute("SELECT COUNT(*) AS c FROM failed_attempts").fetchone()["c"]
    return {
        "total_queries": total,
        "queries_with_failures": with_failures,
        "queries_with_product_info": with_product_info,
        "race_count": races,
        "failed_attempts": attempts,
    }
