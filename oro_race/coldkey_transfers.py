#!/usr/bin/env python3
"""Fetch and cache coldkey transfer pair information."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from race_db import (
    DEFAULT_DB,
    get_coldkey_transfer_pairs,
    get_coldkeys_needing_transfer_pairs,
    get_stored_coldkeys,
    init_db,
    save_coldkey_transfer_pairs,
)

TAOSTATS_TRANSFER_API = "https://api.taostats.io/api/transfer/v1"
ARCHIVE_ENDPOINT = "wss://archive.chain.opentensor.ai:443"
RECENT_BLOCK_SCAN = 80


def _get_json(url: str, headers: dict | None = None, timeout: int = 60) -> dict | list:
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def coldkey_transfers_url(coldkey: str) -> str:
    return f"https://taostats.io/account/{coldkey}/transfers"


def _normalize_transfer_record(coldkey: str, counterparty: str, direction: str, amount_rao: str, **extra) -> dict:
    return {
        "coldkey": coldkey,
        "counterparty": counterparty,
        "direction": direction,
        "in_count": 1 if direction == "in" else 0,
        "out_count": 1 if direction == "out" else 0,
        "total_amount_rao": str(amount_rao),
        "last_transfer_at": extra.get("transfer_at"),
        "last_block": extra.get("block_number"),
    }


def _merge_pair_records(records: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for record in records:
        key = (record["coldkey"], record["counterparty"])
        current = merged.get(key)
        if not current:
            merged[key] = {
                "coldkey": record["coldkey"],
                "counterparty": record["counterparty"],
                "in_count": int(record.get("in_count") or 0),
                "out_count": int(record.get("out_count") or 0),
                "total_amount_rao": int(record.get("total_amount_rao") or 0),
                "last_transfer_at": record.get("last_transfer_at"),
                "last_block": record.get("last_block"),
            }
            continue
        current["in_count"] += int(record.get("in_count") or 0)
        current["out_count"] += int(record.get("out_count") or 0)
        current["total_amount_rao"] += int(record.get("total_amount_rao") or 0)
        if record.get("last_transfer_at") and (
            not current.get("last_transfer_at") or record["last_transfer_at"] > current["last_transfer_at"]
        ):
            current["last_transfer_at"] = record["last_transfer_at"]
            current["last_block"] = record.get("last_block")
    result = []
    for item in merged.values():
        item["total_amount_rao"] = str(item["total_amount_rao"])
        result.append(item)
    result.sort(
        key=lambda row: (
            -(row["in_count"] + row["out_count"]),
            row["counterparty"],
        )
    )
    return result


def fetch_transfers_taostats(coldkey: str, api_key: str, limit: int = 50) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "address": coldkey,
            "limit": limit,
            "network": "finney",
        }
    )
    headers = {"Authorization": api_key, "Accept": "application/json"}
    payload = _get_json(f"{TAOSTATS_TRANSFER_API}?{params}", headers=headers)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []

    records: list[dict] = []
    for row in rows:
        from_addr = (
            (row.get("from") or {}).get("ss58")
            or row.get("from_ss58")
            or row.get("from_address")
        )
        to_addr = (
            (row.get("to") or {}).get("ss58")
            or row.get("to_ss58")
            or row.get("to_address")
        )
        amount = str(row.get("amount") or row.get("amount_rao") or "0")
        transfer_at = row.get("timestamp") or row.get("transfer_at")
        block_number = row.get("block_number") or row.get("block")
        if from_addr == coldkey and to_addr:
            records.append(
                _normalize_transfer_record(
                    coldkey,
                    to_addr,
                    "out",
                    amount,
                    transfer_at=transfer_at,
                    block_number=block_number,
                )
            )
        if to_addr == coldkey and from_addr:
            records.append(
                _normalize_transfer_record(
                    coldkey,
                    from_addr,
                    "in",
                    amount,
                    transfer_at=transfer_at,
                    block_number=block_number,
                )
            )
    return _merge_pair_records(records)


def fetch_transfers_recent_blocks(coldkey: str, coldkeys: set[str], blocks: int = RECENT_BLOCK_SCAN) -> list[dict]:
    all_pairs = fetch_all_winner_transfers_recent_blocks(coldkeys, blocks=blocks)
    return all_pairs.get(coldkey, [])


def fetch_all_winner_transfers_recent_blocks(
    coldkeys: set[str],
    blocks: int = RECENT_BLOCK_SCAN,
) -> dict[str, list[dict]]:
    if not coldkeys:
        return {}

    try:
        import bittensor as bt

        subtensor = bt.Subtensor(network="finney", archive_endpoints=[ARCHIVE_ENDPOINT])
    except Exception:
        return {coldkey: [] for coldkey in coldkeys}

    current = subtensor.get_current_block()
    records: list[dict] = []
    for block_number in range(max(0, current - blocks), current):
        block_hash = subtensor.substrate.get_block_hash(block_number)
        for event in subtensor.substrate.get_events(block_hash):
            if event.get("module_id") != "Balances" or event.get("event_id") != "Transfer":
                continue
            attrs = event.get("attributes") or {}
            from_addr = attrs.get("from")
            to_addr = attrs.get("to")
            amount = str(attrs.get("amount") or "0")
            if from_addr in coldkeys and to_addr:
                records.append(_normalize_transfer_record(from_addr, to_addr, "out", amount, block_number=block_number))
            if to_addr in coldkeys and from_addr:
                records.append(_normalize_transfer_record(to_addr, from_addr, "in", amount, block_number=block_number))

    merged = _merge_pair_records(records)
    by_coldkey: dict[str, list[dict]] = {coldkey: [] for coldkey in coldkeys}
    for pair in merged:
        by_coldkey.setdefault(pair["coldkey"], []).append(pair)
    for coldkey in by_coldkey:
        by_coldkey[coldkey].sort(
            key=lambda row: (-(row["in_count"] + row["out_count"]), row["counterparty"])
        )
    return by_coldkey


def fetch_transfer_pairs_for_coldkey(
    coldkey: str,
    coldkeys: set[str],
    api_key: str | None = None,
) -> list[dict]:
    if api_key:
        try:
            pairs = fetch_transfers_taostats(coldkey, api_key)
            if pairs:
                return pairs
        except Exception:
            pass
    return fetch_transfers_recent_blocks(coldkey, coldkeys)


def sync_coldkey_transfer_pairs(db_path: Path = DEFAULT_DB) -> dict:
    init_db(db_path)
    coldkeys = get_stored_coldkeys(db_path)
    pending = get_coldkeys_needing_transfer_pairs(db_path)
    if not pending:
        return {"fetched": 0, "already_cached": len(coldkeys), "errors": []}

    api_key = os.getenv("TAOSTATS_API_KEY", "").strip() or None
    fetched = 0
    errors: list[str] = []

    if api_key:
        for coldkey in pending:
            try:
                pairs = fetch_transfer_pairs_for_coldkey(coldkey, coldkeys, api_key=api_key)
                save_coldkey_transfer_pairs(db_path, coldkey, pairs)
                fetched += 1
            except Exception as exc:
                errors.append(f"{coldkey[:8]}...: {exc}")
            time.sleep(0.15)
    else:
        try:
            recent_pairs = fetch_all_winner_transfers_recent_blocks(coldkeys)
            for coldkey in pending:
                pairs = recent_pairs.get(coldkey, [])
                save_coldkey_transfer_pairs(db_path, coldkey, pairs)
                fetched += 1
        except Exception as exc:
            errors.append(f"recent_blocks: {exc}")

    return {
        "fetched": fetched,
        "already_cached": len(coldkeys) - len(pending),
        "source": "taostats" if api_key else "recent_blocks",
        "errors": errors,
    }


def build_transfer_indexes(pairs: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    by_coldkey: dict[str, list[dict]] = {}
    for pair in pairs:
        by_coldkey.setdefault(pair["coldkey"], []).append(pair)

    winner_links: dict[tuple[str, str], dict] = {}
    coldkeys = set(by_coldkey)
    for pair in pairs:
        coldkey = pair["coldkey"]
        counterparty = pair["counterparty"]
        if counterparty not in coldkeys:
            continue
        a, b = sorted((coldkey, counterparty))
        key = (a, b)
        current = winner_links.get(key)
        if not current:
            winner_links[key] = {
                "coldkey_a": a,
                "coldkey_b": b,
                "a_to_b": 0,
                "b_to_a": 0,
                "total_transfers": 0,
            }
            current = winner_links[key]
        if coldkey == a and counterparty == b:
            current["a_to_b"] += int(pair.get("out_count") or 0)
        elif coldkey == b and counterparty == a:
            current["b_to_a"] += int(pair.get("out_count") or 0)
        current["total_transfers"] = current["a_to_b"] + current["b_to_a"]

    linked_pairs = sorted(
        winner_links.values(),
        key=lambda row: (-row["total_transfers"], row["coldkey_a"], row["coldkey_b"]),
    )
    return by_coldkey, linked_pairs


def serialize_transfer_pair(pair: dict) -> dict:
    total = int(pair.get("in_count") or 0) + int(pair.get("out_count") or 0)
    direction = "both"
    if pair.get("in_count") and not pair.get("out_count"):
        direction = "in"
    elif pair.get("out_count") and not pair.get("in_count"):
        direction = "out"
    return {
        **pair,
        "total_transfers": total,
        "direction": direction,
        "counterparty_url": coldkey_transfers_url(pair["counterparty"]),
        "transfers_url": coldkey_transfers_url(pair["coldkey"]),
    }
