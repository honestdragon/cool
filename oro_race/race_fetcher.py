#!/usr/bin/env python3
"""Fetch ORO subnet race winners and persist them locally."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from race_db import DEFAULT_DB, get_stored_race_numbers, init_db, save_winner

DEFAULT_BASE_URL = "https://api.oroagents.com"


def _get_json(url: str, timeout: int = 60, retries: int = 5) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt + 1 < retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            raise


def fetch_race_history(base_url: str = DEFAULT_BASE_URL, limit: int = 100) -> list[dict]:
    races: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        params = urllib.parse.urlencode({"limit": limit, "offset": offset})
        data = _get_json(f"{base_url}/v1/public/races/history?{params}")
        total = data["total"]
        batch = data.get("races") or []
        if not batch:
            break
        races.extend(batch)
        offset += len(batch)
        if len(batch) < limit:
            break
        time.sleep(0.1)
    return races


def fetch_race_detail(base_url: str, race_id: str) -> dict:
    return _get_json(f"{base_url}/v1/public/races/{race_id}")


def build_hotkey_coldkey_map(hotkeys: list[str] | None = None) -> dict[str, str]:
    try:
        import bittensor as bt

        subtensor = bt.Subtensor(network="finney")
        mapping: dict[str, str] = {}

        if hotkeys is None:
            metagraph = subtensor.metagraph(netuid=15, lite=True)
            for hotkey, coldkey in zip(metagraph.hotkeys, metagraph.coldkeys, strict=False):
                if hotkey and coldkey:
                    mapping[hotkey] = coldkey
            return mapping

        for hotkey in hotkeys:
            if not hotkey or hotkey in mapping:
                continue
            try:
                owner = subtensor.get_hotkey_owner(hotkey_ss58=hotkey)
                if owner:
                    mapping[hotkey] = owner
            except Exception:
                continue
        return mapping
    except Exception:
        return {}


def extract_winner_record(detail: dict) -> dict | None:
    race = detail.get("race") or {}
    race_number = race.get("race_number")
    race_id = race.get("race_id")
    if race_number is None or not race_id:
        return None

    winner = next(
        (item for item in detail.get("qualifiers") or [] if item.get("race_rank") == 1),
        None,
    )
    if winner:
        return {
            "race_number": int(race_number),
            "race_id": str(race_id),
            "agent_name": winner.get("agent_name") or race.get("winner_agent_name"),
            "miner_hotkey": winner.get("miner_hotkey"),
            "winner_score": winner.get("race_score") or race.get("winner_score"),
            "race_completed_at": race.get("race_completed_at"),
        }

    if race.get("winner_agent_name"):
        return {
            "race_number": int(race_number),
            "race_id": str(race_id),
            "agent_name": race.get("winner_agent_name"),
            "miner_hotkey": None,
            "winner_score": race.get("winner_score"),
            "race_completed_at": race.get("race_completed_at"),
        }
    return None


def sync_race_winners(
    db_path: Path = DEFAULT_DB,
    base_url: str = DEFAULT_BASE_URL,
    race_numbers: set[int] | None = None,
) -> dict:
    """Fetch winners for races not already stored. Existing rows are never refetched."""
    init_db(db_path)
    stored = get_stored_race_numbers(db_path)

    history = fetch_race_history(base_url)
    candidates = [
        race
        for race in history
        if race.get("status") == "RACE_COMPLETE"
        and race.get("winner_agent_version_id")
        and int(race["race_number"]) not in stored
    ]
    if race_numbers is not None:
        candidates = [race for race in candidates if int(race["race_number"]) in race_numbers]

    coldkey_map = build_hotkey_coldkey_map()
    fetched = 0
    errors: list[str] = []

    for race in candidates:
        race_number = int(race["race_number"])
        race_id = race["race_id"]
        try:
            detail = fetch_race_detail(base_url, race_id)
            record = extract_winner_record(detail)
            if not record:
                errors.append(f"race {race_number}: no winner found")
                continue
            hotkey = record.get("miner_hotkey")
            if hotkey and hotkey not in coldkey_map:
                coldkey_map.update(build_hotkey_coldkey_map([hotkey]))
            record["miner_coldkey"] = coldkey_map.get(hotkey) if hotkey else None
            save_winner(db_path, record)
            fetched += 1
        except Exception as exc:
            errors.append(f"race {race_number}: {exc}")
        time.sleep(0.2)

    return {
        "fetched": fetched,
        "already_stored": len(stored),
        "total_stored": len(get_stored_race_numbers(db_path)),
        "pending_before": len(candidates),
        "errors": errors,
    }
