#!/usr/bin/env python3
"""Fetch and cache coldkey timing metadata from chain, races, and taostats."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from race_db import (
    DEFAULT_DB,
    get_all_winners,
    get_coldkey_meta_map,
    get_coldkeys_needing_created_date,
    get_coldkeys_needing_meta,
    init_db,
    save_coldkey_meta,
    update_coldkey_created_at,
    update_missing_coldkeys,
)

ARCHIVE_ENDPOINT = "wss://archive.chain.opentensor.ai:443"
NETUID = 15
TAOSTATS_ACCOUNT_URL = "https://taostats.io/account/{coldkey}"
CREATED_DATE_RE = re.compile(r'created_on_date\\":\\"([^\\]+)\\"')


def _format_block_timestamp(timestamp_ms: int | float | None) -> str | None:
    if not timestamp_ms:
        return None
    return datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=timezone.utc).isoformat()


def _short_date(iso_value: str | None) -> str | None:
    if not iso_value:
        return None
    return iso_value[:10]


def build_first_win_index(winners: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for winner in winners:
        coldkey = winner.get("miner_coldkey")
        if not coldkey:
            continue
        race_number = int(winner["race_number"])
        completed_at = winner.get("race_completed_at")
        current = index.get(coldkey)
        if current is None or race_number < current["first_win_race"]:
            index[coldkey] = {
                "first_win_race": race_number,
                "first_win_at": completed_at,
                "miner_hotkey": winner.get("miner_hotkey"),
            }
    return index


def fetch_coldkey_created_date(coldkey: str) -> str | None:
    """Read coldkey created date from the public taostats account page."""
    url = TAOSTATS_ACCOUNT_URL.format(coldkey=coldkey)
    req = urllib.request.Request(url, headers={"User-Agent": "oro-race-viewer/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        return None

    match = CREATED_DATE_RE.search(html)
    if not match:
        return None
    return match.group(1)


def lookup_subnet_registration(hotkey: str, subtensor, hotkey_to_uid: dict[str, int]) -> dict | None:
    uid = hotkey_to_uid.get(hotkey)
    if uid is None:
        return None
    block = int(subtensor.query_subtensor("BlockAtRegistration", params=[NETUID, uid]))
    block_info = subtensor.get_block_info(block=block)
    registered_at = _format_block_timestamp(block_info.timestamp)
    return {
        "subnet_reg_block": block,
        "subnet_reg_at": registered_at,
    }


def _build_subtensor_context():
    import bittensor as bt

    subtensor = bt.Subtensor(network="finney", archive_endpoints=[ARCHIVE_ENDPOINT])
    metagraph = subtensor.metagraph(netuid=NETUID, lite=True)
    hotkey_to_uid = {hotkey: uid for uid, hotkey in enumerate(metagraph.hotkeys) if hotkey}
    return subtensor, hotkey_to_uid


def sync_coldkey_created_dates(db_path: Path = DEFAULT_DB) -> dict:
    pending = get_coldkeys_needing_created_date(db_path)
    fetched = 0
    errors: list[str] = []

    for coldkey in pending:
        try:
            created_date = fetch_coldkey_created_date(coldkey)
            if not created_date:
                errors.append(f"{coldkey[:8]}...: created date not found")
                continue
            update_coldkey_created_at(db_path, coldkey, created_date)
            fetched += 1
        except Exception as exc:
            errors.append(f"{coldkey[:8]}...: {exc}")
        time.sleep(0.25)

    return {"fetched": fetched, "pending": len(pending), "errors": errors}


def sync_coldkey_meta(db_path: Path = DEFAULT_DB) -> dict:
    """Resolve coldkey metadata for keys not already stored."""
    init_db(db_path)
    updated_coldkeys = update_missing_coldkeys(db_path)
    winners = get_all_winners(db_path)
    first_win_index = build_first_win_index(winners)
    pending = get_coldkeys_needing_meta(db_path)

    subtensor = None
    hotkey_to_uid: dict[str, int] = {}
    if pending:
        try:
            subtensor, hotkey_to_uid = _build_subtensor_context()
        except Exception as exc:
            return {
                "fetched": 0,
                "backfilled_coldkeys": updated_coldkeys,
                "total_cached": len(get_coldkey_meta_map(db_path)),
                "created_dates": sync_coldkey_created_dates(db_path),
                "errors": [f"chain lookup unavailable: {exc}"],
            }

    fetched = 0
    errors: list[str] = []

    for coldkey in pending:
        first_win = first_win_index.get(coldkey, {})
        hotkey = first_win.get("miner_hotkey")
        record = {
            "coldkey": coldkey,
            "first_win_race": first_win.get("first_win_race"),
            "first_win_at": first_win.get("first_win_at"),
            "subnet_reg_block": None,
            "subnet_reg_at": None,
            "coldkey_created_at": fetch_coldkey_created_date(coldkey),
        }
        if hotkey and subtensor is not None:
            try:
                registration = lookup_subnet_registration(hotkey, subtensor, hotkey_to_uid)
                if registration:
                    record.update(registration)
            except Exception as exc:
                errors.append(f"{coldkey[:8]}...: {exc}")

        save_coldkey_meta(db_path, record)
        fetched += 1
        time.sleep(0.25)

    created_dates = sync_coldkey_created_dates(db_path)

    return {
        "fetched": fetched,
        "backfilled_coldkeys": updated_coldkeys,
        "total_cached": len(get_coldkey_meta_map(db_path)),
        "created_dates": created_dates,
        "errors": errors + created_dates.get("errors", []),
    }


def coldkey_account_url(coldkey: str) -> str:
    return TAOSTATS_ACCOUNT_URL.format(coldkey=coldkey)


def serialize_coldkey_meta(meta: dict) -> dict:
    created_at = meta.get("coldkey_created_at")
    return {
        **meta,
        "coldkey_created_date": _short_date(created_at) if created_at and len(created_at) <= 10 else created_at,
        "subnet_reg_date": _short_date(meta.get("subnet_reg_at")),
        "first_win_date": _short_date(meta.get("first_win_at")),
        "account_url": coldkey_account_url(meta["coldkey"]),
    }
