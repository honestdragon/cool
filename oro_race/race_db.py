#!/usr/bin/env python3
"""SQLite storage for per-race ORO subnet winner data."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "race_winners.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DEFAULT_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS race_winners (
                race_number INTEGER PRIMARY KEY,
                race_id TEXT NOT NULL,
                agent_name TEXT,
                miner_hotkey TEXT,
                miner_coldkey TEXT,
                winner_score REAL,
                race_completed_at TEXT,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coldkey_meta (
                coldkey TEXT PRIMARY KEY,
                subnet_reg_block INTEGER,
                subnet_reg_at TEXT,
                coldkey_created_at TEXT,
                first_win_race INTEGER,
                first_win_at TEXT,
                fetched_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE coldkey_meta ADD COLUMN coldkey_created_at TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coldkey_transfer_pairs (
                coldkey TEXT NOT NULL,
                counterparty TEXT NOT NULL,
                in_count INTEGER NOT NULL DEFAULT 0,
                out_count INTEGER NOT NULL DEFAULT 0,
                total_amount_rao TEXT NOT NULL DEFAULT '0',
                last_transfer_at TEXT,
                last_block INTEGER,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (coldkey, counterparty)
            )
            """
        )
        conn.commit()


def get_stored_race_numbers(db_path: Path = DEFAULT_DB) -> set[int]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT race_number FROM race_winners").fetchall()
    return {int(row["race_number"]) for row in rows}


def get_winner(db_path: Path, race_number: int) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM race_winners WHERE race_number = ?",
            (race_number,),
        ).fetchone()
    return dict(row) if row else None


def get_all_winners(db_path: Path = DEFAULT_DB) -> list[dict]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM race_winners ORDER BY race_number ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_winners_by_race(db_path: Path = DEFAULT_DB) -> dict[int, dict]:
    return {item["race_number"]: item for item in get_all_winners(db_path)}


def update_missing_coldkeys(db_path: Path = DEFAULT_DB) -> int:
    """Backfill miner_coldkey on stored winners using chain hotkey ownership."""
    try:
        from race_fetcher import build_hotkey_coldkey_map
    except Exception:
        return 0

    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT race_number, miner_hotkey FROM race_winners WHERE miner_coldkey IS NULL OR miner_coldkey = ''"
        ).fetchall()

    hotkeys = [str(row["miner_hotkey"]) for row in rows if row["miner_hotkey"]]
    if not hotkeys:
        return 0

    coldkey_map = build_hotkey_coldkey_map(hotkeys)
    if not coldkey_map:
        return 0

    updated = 0
    with _connect(db_path) as conn:
        for row in rows:
            hotkey = row["miner_hotkey"]
            coldkey = coldkey_map.get(hotkey) if hotkey else None
            if not coldkey:
                continue
            conn.execute(
                "UPDATE race_winners SET miner_coldkey = ? WHERE race_number = ?",
                (coldkey, int(row["race_number"])),
            )
            updated += 1
        conn.commit()
    return updated


def get_stored_coldkeys(db_path: Path = DEFAULT_DB) -> set[str]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT miner_coldkey FROM race_winners WHERE miner_coldkey IS NOT NULL AND miner_coldkey != ''"
        ).fetchall()
    return {str(row["miner_coldkey"]) for row in rows}


def get_coldkeys_needing_transfer_pairs(db_path: Path = DEFAULT_DB) -> list[str]:
    init_db(db_path)
    stored = get_stored_coldkeys(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT coldkey FROM coldkey_transfer_pairs").fetchall()
    cached = {str(row["coldkey"]) for row in rows}
    return sorted(stored - cached)


def get_coldkey_transfer_pairs(db_path: Path = DEFAULT_DB) -> list[dict]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM coldkey_transfer_pairs
            WHERE counterparty != '__none__'
            ORDER BY (in_count + out_count) DESC, coldkey ASC, counterparty ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def save_coldkey_transfer_pairs(db_path: Path, coldkey: str, pairs: list[dict]) -> None:
    init_db(db_path)
    fetched_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        for pair in pairs:
            conn.execute(
                """
                INSERT INTO coldkey_transfer_pairs (
                    coldkey,
                    counterparty,
                    in_count,
                    out_count,
                    total_amount_rao,
                    last_transfer_at,
                    last_block,
                    fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coldkey, counterparty) DO NOTHING
                """,
                (
                    coldkey,
                    pair["counterparty"],
                    int(pair.get("in_count") or 0),
                    int(pair.get("out_count") or 0),
                    str(pair.get("total_amount_rao") or "0"),
                    pair.get("last_transfer_at"),
                    pair.get("last_block"),
                    fetched_at,
                ),
            )
        if not pairs:
            conn.execute(
                """
                INSERT INTO coldkey_transfer_pairs (
                    coldkey, counterparty, in_count, out_count, total_amount_rao, fetched_at
                ) VALUES (?, '__none__', 0, 0, '0', ?)
                ON CONFLICT(coldkey, counterparty) DO NOTHING
                """,
                (coldkey, fetched_at),
            )
        conn.commit()


def get_coldkeys_needing_created_date(db_path: Path = DEFAULT_DB) -> list[str]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT coldkey FROM coldkey_meta
            WHERE coldkey_created_at IS NULL OR coldkey_created_at = ''
            ORDER BY coldkey ASC
            """
        ).fetchall()
    return [str(row["coldkey"]) for row in rows]


def get_coldkeys_needing_meta(db_path: Path = DEFAULT_DB) -> list[str]:
    init_db(db_path)
    stored = get_stored_coldkeys(db_path)
    cached = set(get_coldkey_meta_map(db_path))
    return sorted(stored - cached)


def get_coldkey_meta_map(db_path: Path = DEFAULT_DB) -> dict[str, dict]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM coldkey_meta").fetchall()
    return {str(row["coldkey"]): dict(row) for row in rows}


def get_all_coldkey_meta(db_path: Path = DEFAULT_DB) -> list[dict]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM coldkey_meta ORDER BY first_win_race ASC").fetchall()
    return [dict(row) for row in rows]


def save_coldkey_meta(db_path: Path, record: dict) -> None:
    init_db(db_path)
    fetched_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO coldkey_meta (
                coldkey,
                subnet_reg_block,
                subnet_reg_at,
                coldkey_created_at,
                first_win_race,
                first_win_at,
                fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(coldkey) DO NOTHING
            """,
            (
                str(record["coldkey"]),
                record.get("subnet_reg_block"),
                record.get("subnet_reg_at"),
                record.get("coldkey_created_at"),
                record.get("first_win_race"),
                record.get("first_win_at"),
                fetched_at,
            ),
        )
        conn.commit()


def update_coldkey_created_at(db_path: Path, coldkey: str, created_at: str) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE coldkey_meta SET coldkey_created_at = ? WHERE coldkey = ?",
            (created_at, coldkey),
        )
        conn.commit()


def save_winner(db_path: Path, record: dict) -> None:
    init_db(db_path)
    fetched_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO race_winners (
                race_number,
                race_id,
                agent_name,
                miner_hotkey,
                miner_coldkey,
                winner_score,
                race_completed_at,
                fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_number) DO NOTHING
            """,
            (
                int(record["race_number"]),
                str(record["race_id"]),
                record.get("agent_name"),
                record.get("miner_hotkey"),
                record.get("miner_coldkey"),
                record.get("winner_score"),
                record.get("race_completed_at"),
                fetched_at,
            ),
        )
        conn.commit()
