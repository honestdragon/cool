#!/usr/bin/env python3
"""SQLite cache for completed race agent evaluation payloads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "race_agent_cache.db"

CACHEABLE_STATUSES = frozenset({"RACE_COMPLETE"})


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DEFAULT_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS race_agent_cache (
                race_number INTEGER PRIMARY KEY,
                race_id TEXT NOT NULL,
                race_status TEXT NOT NULL,
                agent_count INTEGER NOT NULL,
                problem_count INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def is_cacheable_status(status: str | None) -> bool:
    return (status or "") in CACHEABLE_STATUSES


def get_cached_race_payload(race_number: int, db_path: Path = DEFAULT_DB) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM race_agent_cache WHERE race_number = ?",
            (int(race_number),),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def save_race_payload(payload: dict, db_path: Path = DEFAULT_DB) -> None:
    init_db(db_path)
    race = payload.get("race") or {}
    summary = payload.get("summary") or {}
    fetched_at = payload.get("fetched_at") or datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO race_agent_cache (
                race_number,
                race_id,
                race_status,
                agent_count,
                problem_count,
                payload_json,
                fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_number) DO UPDATE SET
                race_id = excluded.race_id,
                race_status = excluded.race_status,
                agent_count = excluded.agent_count,
                problem_count = excluded.problem_count,
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at
            """,
            (
                int(race.get("race_number") or 0),
                str(race.get("race_id") or ""),
                str(race.get("status") or ""),
                int(summary.get("agent_count_loaded") or len(payload.get("agents") or [])),
                int(summary.get("problem_count") or len(payload.get("problems") or [])),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                fetched_at,
            ),
        )
        conn.commit()


def list_cached_races(db_path: Path = DEFAULT_DB) -> list[dict]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT race_number, race_status, agent_count, problem_count, fetched_at
            FROM race_agent_cache
            ORDER BY race_number DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def delete_race_cache(race_number: int, db_path: Path = DEFAULT_DB) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM race_agent_cache WHERE race_number = ?", (int(race_number),))
        conn.commit()
