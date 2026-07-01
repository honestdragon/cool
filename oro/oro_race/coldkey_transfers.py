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
    get_coldkeys_needing_transfer_pairs_from,
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


def sync_coldkey_transfer_pairs_for(coldkeys: set[str], db_path: Path = DEFAULT_DB) -> dict:
    """Fetch and cache transfer pairs for an arbitrary coldkey set."""
    init_db(db_path)
    if not coldkeys:
        return {"fetched": 0, "already_cached": 0, "source": "none", "errors": []}

    pending = get_coldkeys_needing_transfer_pairs_from(db_path, coldkeys)
    if not pending:
        return {
            "fetched": 0,
            "already_cached": len(coldkeys),
            "source": "cache",
            "errors": [],
        }

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


def build_coldkey_transfer_groups(
    coldkeys: set[str],
    pairs: list[dict],
) -> tuple[dict[str, dict], list[dict]]:
    """Group registered coldkeys connected by on-chain transfers."""
    from collections import defaultdict

    parent: dict[str, str] = {coldkey: coldkey for coldkey in coldkeys}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != node:
            nxt = parent[node]
            parent[node] = root
            node = nxt
        return root

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    coldkey_set = set(coldkeys)
    internal_pairs: list[dict] = []
    for pair in pairs:
        coldkey = pair["coldkey"]
        counterparty = pair["counterparty"]
        if coldkey in coldkey_set and counterparty in coldkey_set:
            union(coldkey, counterparty)
            internal_pairs.append(pair)

    grouped: dict[str, list[str]] = defaultdict(list)
    for coldkey in coldkeys:
        grouped[find(coldkey)].append(coldkey)

    by_coldkey: dict[str, dict] = {}
    group_summaries: list[dict] = []
    linked_groups = sorted(
        ((root, sorted(members)) for root, members in grouped.items() if len(members) > 1),
        key=lambda item: (-len(item[1]), item[1][0]),
    )

    for index, (_root, members) in enumerate(linked_groups, start=1):
        label = f"G{index}"
        transfer_count = sum(
            int(pair.get("in_count") or 0) + int(pair.get("out_count") or 0)
            for pair in internal_pairs
            if pair["coldkey"] in members and pair["counterparty"] in members
        )
        summary = {
            "transfer_group_id": index,
            "transfer_group_label": label,
            "coldkeys": members,
            "coldkey_count": len(members),
            "transfer_count": transfer_count,
        }
        group_summaries.append(summary)
        for coldkey in members:
            by_coldkey[coldkey] = {
                "transfer_group_id": index,
                "transfer_group_label": label,
                "transfer_group_size": len(members),
                "transfer_group_coldkeys": members,
                "transfer_group_transfer_count": transfer_count,
            }

    for coldkey in coldkeys:
        if coldkey in by_coldkey:
            continue
        by_coldkey[coldkey] = {
            "transfer_group_id": None,
            "transfer_group_label": None,
            "transfer_group_size": 1,
            "transfer_group_coldkeys": [coldkey],
            "transfer_group_transfer_count": 0,
        }

    return by_coldkey, group_summaries


def build_registration_transfer_payload(
    coldkeys: set[str],
    db_path: Path = DEFAULT_DB,
    *,
    sync: bool = True,
) -> dict:
    from race_db import get_transfer_pairs_between_coldkeys

    sync_result = (
        sync_coldkey_transfer_pairs_for(coldkeys, db_path)
        if sync
        else {"fetched": 0, "source": "cache", "errors": []}
    )
    pairs = get_transfer_pairs_between_coldkeys(db_path, coldkeys)
    by_coldkey_meta, group_summaries = build_coldkey_transfer_groups(coldkeys, pairs)
    by_coldkey_pairs, linked_pairs = build_transfer_indexes(pairs)
    source = sync_result.get("source") or ("taostats" if os.getenv("TAOSTATS_API_KEY") else "recent_blocks")
    return {
        "by_coldkey": by_coldkey_meta,
        "groups": group_summaries,
        "pairs": [serialize_transfer_pair(item) for item in pairs],
        "linked_pairs": linked_pairs,
        "by_coldkey_pairs": {
            coldkey: [serialize_transfer_pair(item) for item in rows]
            for coldkey, rows in by_coldkey_pairs.items()
        },
        "pair_count": len(pairs),
        "linked_group_count": len(group_summaries),
        "linked_coldkey_count": sum(group["coldkey_count"] for group in group_summaries),
        "sync": sync_result,
        "source": source,
        "source_note": (
            "Full transfer history via Taostats API"
            if os.getenv("TAOSTATS_API_KEY")
            else "Recent on-chain blocks only (set TAOSTATS_API_KEY for full history)"
        ),
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
