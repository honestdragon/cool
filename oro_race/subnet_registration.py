#!/usr/bin/env python3
"""Fetch and aggregate SN15 metagraph registrations by coldkey."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

NETUID = 15
BLOCKS_PER_DAY = 7200  # ~12s block time on finney
ARCHIVE_ENDPOINT = "wss://archive.chain.opentensor.ai:443"
TAOSTATS_REGISTRATION_API = "https://api.taostats.io/api/subnet/neuron/registration/v1"
RAO_PER_TAO = 1_000_000_000
BLOCK_TIME_CACHE_PATH = Path(__file__).resolve().parent / "data" / "sn15_block_times.json"
TMC_NEURONS_API = "https://api.taomarketcap.com/public/v1/subnets/neurons/{netuid}/"
_block_time_cache_lock = threading.Lock()

TEAM_COLDKEYS: dict[str, str] = {
    "Hercules": "5ENT46iaGbeFUkogaFrFJCHqTBu6Ear9Av6FbHwjRcd2SWKy",
    "Honest": "5GYtHdUdpkcMf6HgMUScYdwU4BmBuUnsmF4AbCdoCaWAMhMi",
    "Mark": "5CMGJv3DzXDVWeLpzcszd9EoXhgbWGyaG9tzQTxUEFD4J6CB",
    "Owner": "5HWNwnU2VThGV7oCKiKbpcPA4atAUWEGzgUpyt5ejxL3Bvq2",
    "Owner 2": "5DAseYn5vMzfKwFvgGWzA2QnVoc515BeKrBGsM5JTxWZ4V9D",
    "Robin": "5Ggye5xBjWxBQtbrcHaYAW7iZXxZ2Z8k2wwsNvh8usMaa5kb",
    "Xiwang": "5GstSjQuW2mamR6dzh5pDeVY2CZduDUt4jgLvEcVRnXSmoEj",
    "ben": "5DUCM3pQiAEVumtGAwMC5hX6P7ipDuZJyJiTTMNmXp389Hg1",
    "jk": "5FqcMAmJrtwWUYdtwQ1JRXSxTLwJUXdddE9Mqy8qJUhgUEe8",
    "rabbit": "5Hguif4pF3Wao48nhLSsgZS6uvPMccfHNKUoWzW7SriqUVFT",
}
COLDKEY_TO_TEAM_NAME = {coldkey: name for name, coldkey in TEAM_COLDKEYS.items()}


def _get_json(url: str, headers: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _tao_from_rao(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    try:
        return round(int(value) / RAO_PER_TAO, 6)
    except (TypeError, ValueError):
        return None


def _current_registration_cost_tao(subtensor, netuid: int) -> float | None:
    try:
        recycle = subtensor.recycle(netuid=netuid)
        if recycle is None:
            return None
        return round(float(recycle.tao), 6)
    except Exception:
        return None


def _fetch_taostats_registration_costs(
    netuid: int,
    api_key: str,
    target_hotkeys: set[str],
    *,
    max_pages: int = 80,
) -> tuple[dict[str, dict], dict]:
    """Return hotkey -> registration record for currently registered hotkeys."""
    if not target_hotkeys:
        return {}, {"matched": 0, "pages_fetched": 0, "total_items": 0}

    headers = {"Authorization": api_key, "Accept": "application/json"}
    by_hotkey: dict[str, dict] = {}
    pages_fetched = 0
    total_items = 0

    for page in range(1, max_pages + 1):
        params = urllib.parse.urlencode(
            {
                "netuid": netuid,
                "limit": 200,
                "page": page,
                "order": "timestamp_desc",
            }
        )
        try:
            payload = _get_json(f"{TAOSTATS_REGISTRATION_API}?{params}", headers=headers)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Taostats registration API HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Taostats registration API unreachable: {exc.reason}") from exc

        pagination = payload.get("pagination") or {}
        total_items = int(pagination.get("total_items") or 0)
        rows = payload.get("data") or []
        pages_fetched = page
        if not rows:
            break

        for row in rows:
            hotkey = ((row.get("hotkey") or {}).get("ss58") or "").strip()
            if not hotkey or hotkey in by_hotkey:
                continue
            if hotkey not in target_hotkeys:
                continue
            cost_tao = _tao_from_rao(row.get("registration_cost"))
            by_hotkey[hotkey] = {
                "uid": row.get("uid"),
                "registration_cost_rao": str(row.get("registration_cost") or ""),
                "registration_cost_tao": cost_tao,
                "registered_at": row.get("timestamp"),
                "registration_block": row.get("block_number"),
                "coldkey": ((row.get("coldkey") or {}).get("ss58") or "").strip(),
            }

        if len(by_hotkey) >= len(target_hotkeys):
            break
        if page >= int(pagination.get("total_pages") or page):
            break

    meta = {
        "matched": len(by_hotkey),
        "target_hotkeys": len(target_hotkeys),
        "pages_fetched": pages_fetched,
        "total_items": total_items,
    }
    return by_hotkey, meta


def _attach_registration_costs(
    by_coldkey: dict[str, list[dict]],
    reg_costs: dict[str, dict],
) -> None:
    for miners in by_coldkey.values():
        for miner in miners:
            info = reg_costs.get(miner["hotkey"], {})
            miner["registration_cost_tao"] = info.get("registration_cost_tao")
            miner["registration_cost_rao"] = info.get("registration_cost_rao")
            miner["registered_at"] = info.get("registered_at")
            miner["registration_block"] = info.get("registration_block")


def _summarize_registration_costs(miners: list[dict]) -> dict:
    costs = [item["registration_cost_tao"] for item in miners if item.get("registration_cost_tao") is not None]
    if not costs:
        return {
            "total_registration_cost_tao": None,
            "avg_registration_cost_tao": None,
            "min_registration_cost_tao": None,
            "max_registration_cost_tao": None,
            "known_registration_costs": 0,
        }
    return {
        "total_registration_cost_tao": round(sum(costs), 6),
        "avg_registration_cost_tao": round(sum(costs) / len(costs), 6),
        "min_registration_cost_tao": round(min(costs), 6),
        "max_registration_cost_tao": round(max(costs), 6),
        "known_registration_costs": len(costs),
    }


def _build_hotkey_rows(coldkey_rows: list[dict], team_coldkey_to_name: dict[str, str]) -> list[dict]:
    rows = []
    for coldkey_row in coldkey_rows:
        coldkey = coldkey_row["coldkey"]
        team_name = team_coldkey_to_name.get(coldkey)
        for miner in coldkey_row["miners"]:
            rows.append(
                {
                    "uid": miner["uid"],
                    "hotkey": miner["hotkey"],
                    "coldkey": coldkey,
                    "team_name": team_name,
                    "is_team": bool(team_name),
                    "registration_cost_tao": miner.get("registration_cost_tao"),
                    "registration_cost_rao": miner.get("registration_cost_rao"),
                    "registered_at": miner.get("registered_at"),
                    "registration_block": miner.get("registration_block"),
                    "emission": miner.get("emission"),
                    "daily_tao": miner.get("daily_tao"),
                    "stake": miner.get("stake"),
                    "active": miner.get("active"),
                }
            )
    rows.sort(
        key=lambda row: (
            not row["is_team"],
            row["registration_cost_tao"] is None,
            -(row["registration_cost_tao"] or 0),
            row["uid"],
        )
    )
    return rows


def _format_block_timestamp(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _lookup_registration_times(subtensor, miners: list[dict]) -> dict[int, str | None]:
    """Return uid -> registration timestamp (best effort via archive node)."""
    times: dict[int, str | None] = {}
    for miner in miners:
        uid = miner["uid"]
        try:
            block = int(subtensor.query_subtensor("BlockAtRegistration", params=[NETUID, uid]))
            block_info = subtensor.get_block_info(block=block)
            times[uid] = _format_block_timestamp(block_info.timestamp)
        except Exception:
            times[uid] = None
    return times


def _uid_bucket(uid_count: int) -> str:
    if uid_count == 1:
        return "1 UID"
    if uid_count <= 3:
        return "2-3 UIDs"
    if uid_count <= 7:
        return "4-7 UIDs"
    if uid_count <= 12:
        return "8-12 UIDs"
    return "13+ UIDs"


def _tempos_per_day(tempo: int) -> float:
    if tempo <= 0:
        return 0.0
    return BLOCKS_PER_DAY / tempo


def _emission_to_daily_tao(emission: float, *, tempo: int, alpha_price_tao: float) -> float:
    """Convert metagraph emission (alpha per tempo) to estimated TAO earned per day."""
    return round(float(emission) * _tempos_per_day(tempo) * float(alpha_price_tao), 6)


def _is_validator_uid(metagraph, uid: int) -> bool:
    """True when the UID holds validator permit (exclude from miner emission stats)."""
    permits = getattr(metagraph, "validator_permit", None)
    if permits is None or uid >= len(permits):
        return False
    return bool(permits[uid])


def _miner_emission(metagraph, uid: int) -> float:
    if _is_validator_uid(metagraph, uid):
        return 0.0
    return float(metagraph.emission[uid])


def _assign_ranks(rows: list[dict], value_key: str, rank_key: str) -> None:
    ordered = sorted(rows, key=lambda row: (-row[value_key], row["coldkey"]))
    for index, row in enumerate(ordered, start=1):
        row[rank_key] = index


def _build_distribution(rows: list[dict], value_key: str) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        label = _uid_bucket(row["uid_count"])
        bucket = buckets.setdefault(
            label,
            {
                "label": label,
                "coldkeys": 0,
                "uids": 0,
                "total_emission": 0.0,
                "total_daily_tao": 0.0,
                "total_stake": 0.0,
            },
        )
        bucket["coldkeys"] += 1
        bucket["uids"] += row["uid_count"]
        bucket["total_emission"] += row["total_emission"]
        bucket["total_daily_tao"] += row.get("daily_tao", 0.0)
        bucket["total_stake"] += row["total_stake"]

    order = ["1 UID", "2-3 UIDs", "4-7 UIDs", "8-12 UIDs", "13+ UIDs"]
    total_coldkeys = len(rows) or 1
    total_uids = sum(row["uid_count"] for row in rows) or 1
    distribution = []
    for label in order:
        bucket = buckets.get(label)
        if not bucket:
            continue
        distribution.append(
            {
                **bucket,
                "coldkey_pct": round(100 * bucket["coldkeys"] / total_coldkeys, 1),
                "uid_pct": round(100 * bucket["uids"] / total_uids, 1),
                "total_emission": round(bucket["total_emission"], 6),
                "total_daily_tao": round(bucket["total_daily_tao"], 6),
                "total_stake": round(bucket["total_stake"], 4),
            }
        )
    return distribution


def _build_team_status(coldkey_index: dict[str, dict]) -> list[dict]:
    team_rows = []
    for name, coldkey in TEAM_COLDKEYS.items():
        row = coldkey_index.get(coldkey)
        if row:
            team_rows.append(
                {
                    "name": name,
                    "coldkey": coldkey,
                    "registered": True,
                    "uid_count": row["uid_count"],
                    "active_count": row["active_count"],
                    "uid_rank": row["uid_rank"],
                    "emission_rank": row["emission_rank"],
                    "stake_rank": row["stake_rank"],
                    "incentive_rank": row["incentive_rank"],
                    "uid_pct": row["uid_pct"],
                    "emission_pct": row["emission_pct"],
                    "stake_pct": row["stake_pct"],
                    "incentive_pct": row["incentive_pct"],
                    "total_emission": row["total_emission"],
                    "daily_tao": row["daily_tao"],
                    "daily_tao_rank": row["daily_tao_rank"],
                    "daily_tao_pct": row["daily_tao_pct"],
                    "total_stake": row["total_stake"],
                    "total_incentive": row["total_incentive"],
                    "total_registration_cost_tao": row.get("total_registration_cost_tao"),
                    "avg_registration_cost_tao": row.get("avg_registration_cost_tao"),
                    "known_registration_costs": row.get("known_registration_costs", 0),
                    "uids": row["uids"],
                    "hotkeys": row["hotkeys"],
                    "first_registered_at": row.get("first_registered_at"),
                    "latest_registered_at": row.get("latest_registered_at"),
                    "is_team": True,
                }
            )
        else:
            team_rows.append(
                {
                    "name": name,
                    "coldkey": coldkey,
                    "registered": False,
                    "uid_count": 0,
                    "active_count": 0,
                    "uid_rank": None,
                    "emission_rank": None,
                    "stake_rank": None,
                    "incentive_rank": None,
                    "uid_pct": 0.0,
                    "emission_pct": 0.0,
                    "stake_pct": 0.0,
                    "incentive_pct": 0.0,
                    "total_emission": 0.0,
                    "daily_tao": 0.0,
                    "daily_tao_rank": None,
                    "daily_tao_pct": 0.0,
                    "total_stake": 0.0,
                    "total_incentive": 0.0,
                    "total_registration_cost_tao": None,
                    "avg_registration_cost_tao": None,
                    "known_registration_costs": 0,
                    "uids": [],
                    "hotkeys": [],
                    "first_registered_at": None,
                    "latest_registered_at": None,
                    "is_team": True,
                }
            )

    team_rows.sort(
        key=lambda row: (
            not row["registered"],
            row["uid_rank"] if row["uid_rank"] is not None else 9999,
            row["name"].lower(),
        )
    )
    return team_rows


def fetch_subnet_registration_stats(
    netuid: int = NETUID,
    *,
    include_registration_times: bool = False,
    include_registration_costs: bool | None = None,
) -> dict:
    """Return registered UIDs grouped by coldkey with aggregate statistics."""
    import bittensor as bt

    subtensor = bt.Subtensor(network="finney")
    metagraph = subtensor.metagraph(netuid=netuid, lite=True)
    subnet_info = subtensor.subnet(netuid=netuid)
    tempo = int(subnet_info.tempo or 360)
    alpha_price_tao = float(subnet_info.price or 0.0)
    tempos_per_day = _tempos_per_day(tempo)
    current_registration_cost_tao = _current_registration_cost_tao(subtensor, netuid)

    by_coldkey: dict[str, list[dict]] = defaultdict(list)
    for uid in range(metagraph.n):
        hotkey = metagraph.hotkeys[uid]
        coldkey = metagraph.coldkeys[uid]
        if not hotkey:
            continue
        by_coldkey[coldkey].append(
            {
                "uid": uid,
                "hotkey": hotkey,
                "stake": round(float(metagraph.stake[uid]), 6),
                "incentive": round(float(metagraph.incentive[uid]), 6),
                "is_validator": _is_validator_uid(metagraph, uid),
                "emission": round(_miner_emission(metagraph, uid), 6),
                "daily_tao": _emission_to_daily_tao(
                    _miner_emission(metagraph, uid),
                    tempo=tempo,
                    alpha_price_tao=alpha_price_tao,
                ),
                "active": bool(metagraph.active[uid]),
            }
        )

    total_slots = int(metagraph.n)
    total_registered = sum(len(miners) for miners in by_coldkey.values())
    total_emission = sum(_miner_emission(metagraph, uid) for uid in range(metagraph.n))
    total_daily_tao = sum(
        _emission_to_daily_tao(_miner_emission(metagraph, uid), tempo=tempo, alpha_price_tao=alpha_price_tao)
        for uid in range(metagraph.n)
    )
    total_stake = sum(float(metagraph.stake[uid]) for uid in range(metagraph.n))
    total_incentive = sum(float(metagraph.incentive[uid]) for uid in range(metagraph.n))

    archive_subtensor = None
    if include_registration_times:
        try:
            archive_subtensor = bt.Subtensor(network="finney", archive_endpoints=[ARCHIVE_ENDPOINT])
        except Exception:
            archive_subtensor = None

    api_key = os.getenv("TAOSTATS_API_KEY", "").strip() or None
    should_fetch_costs = include_registration_costs if include_registration_costs is not None else bool(api_key)
    registration_cost_meta = {
        "source": "none",
        "matched": 0,
        "target_hotkeys": total_registered,
        "note": "Set TAOSTATS_API_KEY to load per-hotkey registration burn paid at registration time.",
    }
    if should_fetch_costs:
        if not api_key:
            registration_cost_meta["note"] = "TAOSTATS_API_KEY is required for per-hotkey registration costs."
        else:
            target_hotkeys = {miner["hotkey"] for miners in by_coldkey.values() for miner in miners}
            try:
                reg_costs, registration_cost_meta = _fetch_taostats_registration_costs(
                    netuid,
                    api_key,
                    target_hotkeys,
                )
                registration_cost_meta["source"] = "taostats"
                registration_cost_meta["note"] = (
                    "Historical registration burn per hotkey via Taostats API."
                )
                _attach_registration_costs(by_coldkey, reg_costs)
            except Exception as exc:
                registration_cost_meta["source"] = "taostats_error"
                registration_cost_meta["note"] = str(exc)

    team_coldkey_set = set(TEAM_COLDKEYS.values())
    coldkey_rows = []
    for coldkey, miners in by_coldkey.items():
        miners.sort(key=lambda item: item["uid"])
        uid_count = len(miners)
        total_row_emission = sum(item["emission"] for item in miners)
        total_row_daily_tao = sum(item["daily_tao"] for item in miners)
        total_row_stake = sum(item["stake"] for item in miners)
        total_row_incentive = sum(item["incentive"] for item in miners)

        first_registered_at = None
        latest_registered_at = None
        if archive_subtensor and coldkey in team_coldkey_set:
            reg_times = _lookup_registration_times(archive_subtensor, miners)
            for miner in miners:
                miner["registered_at"] = reg_times.get(miner["uid"])
            valid_times = [value for value in reg_times.values() if value]
            if valid_times:
                first_registered_at = min(valid_times)
                latest_registered_at = max(valid_times)

        cost_summary = _summarize_registration_costs(miners)
        coldkey_rows.append(
            {
                "coldkey": coldkey,
                "uid_count": uid_count,
                "uid_pct": round(100 * uid_count / total_slots, 2) if total_slots else 0.0,
                "active_count": sum(1 for item in miners if item["active"]),
                "total_stake": round(total_row_stake, 6),
                "total_incentive": round(total_row_incentive, 6),
                "total_emission": round(total_row_emission, 6),
                "emission_pct": round(100 * total_row_emission / total_emission, 3) if total_emission else 0.0,
                "daily_tao": round(total_row_daily_tao, 6),
                "daily_tao_pct": round(100 * total_row_daily_tao / total_daily_tao, 3) if total_daily_tao else 0.0,
                "stake_pct": round(100 * total_row_stake / total_stake, 3) if total_stake else 0.0,
                "incentive_pct": round(100 * total_row_incentive / total_incentive, 3) if total_incentive else 0.0,
                "uids": [item["uid"] for item in miners],
                "hotkeys": [item["hotkey"] for item in miners],
                "miners": miners,
                "first_registered_at": first_registered_at,
                "latest_registered_at": latest_registered_at,
                "is_team": coldkey in team_coldkey_set,
                **cost_summary,
            }
        )

    _assign_ranks(coldkey_rows, "uid_count", "uid_rank")
    _assign_ranks(coldkey_rows, "total_emission", "emission_rank")
    _assign_ranks(coldkey_rows, "daily_tao", "daily_tao_rank")
    _assign_ranks(coldkey_rows, "total_stake", "stake_rank")
    _assign_ranks(coldkey_rows, "total_incentive", "incentive_rank")
    cost_rank_rows = [row for row in coldkey_rows if row.get("total_registration_cost_tao") is not None]
    _assign_ranks(cost_rank_rows, "total_registration_cost_tao", "registration_cost_rank")

    coldkey_index = {row["coldkey"]: row for row in coldkey_rows}
    team_status = _build_team_status(coldkey_index)
    team_coldkey_to_name = {coldkey: name for name, coldkey in TEAM_COLDKEYS.items()}

    by_uid_count = sorted(coldkey_rows, key=lambda row: (-row["uid_count"], -row["total_emission"], row["coldkey"]))
    by_emission = sorted(coldkey_rows, key=lambda row: (-row["total_emission"], -row["uid_count"], row["coldkey"]))
    by_daily_tao = sorted(coldkey_rows, key=lambda row: (-row["daily_tao"], -row["uid_count"], row["coldkey"]))
    by_stake = sorted(coldkey_rows, key=lambda row: (-row["total_stake"], -row["uid_count"], row["coldkey"]))
    by_incentive = sorted(coldkey_rows, key=lambda row: (-row["total_incentive"], -row["uid_count"], row["coldkey"]))
    by_reg_cost = sorted(
        cost_rank_rows,
        key=lambda row: (-row["total_registration_cost_tao"], -row["uid_count"], row["coldkey"]),
    )
    hotkey_rows = _build_hotkey_rows(coldkey_rows, team_coldkey_to_name)
    team_hotkey_rows = [row for row in hotkey_rows if row["is_team"]]

    multi_uid_coldkeys = sum(1 for row in coldkey_rows if row["uid_count"] > 1)
    max_uid_count = by_uid_count[0]["uid_count"] if by_uid_count else 0
    team_registered = sum(1 for row in team_status if row["registered"])
    team_uids = sum(row["uid_count"] for row in team_status)
    team_emission_pct = round(sum(row["emission_pct"] for row in team_status), 3)
    team_daily_tao = round(sum(row["daily_tao"] for row in team_status), 6)
    team_daily_tao_pct = round(100 * team_daily_tao / total_daily_tao, 3) if total_daily_tao else 0.0
    team_stake_pct = round(sum(row["stake_pct"] for row in team_status), 3)
    team_uid_pct = round(100 * team_uids / total_slots, 2) if total_slots else 0.0
    team_reg_costs = [
        row["total_registration_cost_tao"]
        for row in team_status
        if row.get("total_registration_cost_tao") is not None
    ]
    team_total_registration_cost_tao = round(sum(team_reg_costs), 6) if team_reg_costs else None
    known_hotkey_costs = sum(1 for row in hotkey_rows if row.get("registration_cost_tao") is not None)

    top5_uid_share = round(
        100 * sum(row["uid_count"] for row in by_uid_count[:5]) / total_slots,
        1,
    ) if total_slots else 0.0
    top5_emission_share = round(sum(row["emission_pct"] for row in by_emission[:5]), 1)
    top5_daily_tao_share = round(sum(row["daily_tao_pct"] for row in by_daily_tao[:5]), 1)
    top10_emission_share = round(sum(row["emission_pct"] for row in by_emission[:10]), 1)
    validator_count = sum(1 for uid in range(metagraph.n) if _is_validator_uid(metagraph, uid))

    return {
        "netuid": netuid,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "bittensor_metagraph",
        "registration_costs": registration_cost_meta,
        "team_coldkeys": TEAM_COLDKEYS,
        "summary": {
            "total_slots": total_slots,
            "registered_count": total_registered,
            "unique_coldkeys": len(coldkey_rows),
            "multi_uid_coldkeys": multi_uid_coldkeys,
            "max_uid_count": max_uid_count,
            "avg_uids_per_coldkey": round(total_registered / len(coldkey_rows), 2) if coldkey_rows else 0.0,
            "total_emission": round(total_emission, 6),
            "total_daily_tao": round(total_daily_tao, 6),
            "validator_count": validator_count,
            "miners_only_emission": True,
            "alpha_price_tao": round(alpha_price_tao, 9),
            "tempo": tempo,
            "tempos_per_day": round(tempos_per_day, 2),
            "total_stake": round(total_stake, 4),
            "total_incentive": round(total_incentive, 6),
            "current_registration_cost_tao": current_registration_cost_tao,
            "known_hotkey_registration_costs": known_hotkey_costs,
            "team_total_registration_cost_tao": team_total_registration_cost_tao,
            "top5_uid_share_pct": top5_uid_share,
            "top5_emission_share_pct": top5_emission_share,
            "top5_daily_tao_share_pct": top5_daily_tao_share,
            "top10_emission_share_pct": top10_emission_share,
            "team_members": len(TEAM_COLDKEYS),
            "team_registered": team_registered,
            "team_uids": team_uids,
            "team_uid_pct": team_uid_pct,
            "team_emission_pct": team_emission_pct,
            "team_daily_tao": team_daily_tao,
            "team_daily_tao_pct": team_daily_tao_pct,
            "team_stake_pct": team_stake_pct,
        },
        "distribution": _build_distribution(coldkey_rows, "uid_count"),
        "team": team_status,
        "hotkeys": hotkey_rows,
        "team_hotkeys": team_hotkey_rows,
        "perspectives": {
            "by_uid_count": by_uid_count,
            "by_emission": by_emission,
            "by_daily_tao": by_daily_tao,
            "by_stake": by_stake,
            "by_incentive": by_incentive,
            "by_reg_cost": by_reg_cost,
        },
        "coldkeys": by_uid_count,
    }


def _annotate_team_fields(row: dict) -> dict:
    coldkey = (row.get("coldkey") or "").strip()
    team_name = COLDKEY_TO_TEAM_NAME.get(coldkey)
    row["team_name"] = team_name
    row["is_team"] = team_name is not None
    return row


def _attach_coldkey_reg_totals(rows: list[dict]) -> None:
    totals: dict[str, float] = defaultdict(float)
    known_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        coldkey = (row.get("coldkey") or "").strip()
        cost = row.get("registration_cost_tao")
        if coldkey and cost is not None:
            totals[coldkey] += float(cost)
            known_counts[coldkey] += 1
    for row in rows:
        coldkey = (row.get("coldkey") or "").strip()
        if coldkey and known_counts.get(coldkey, 0) > 0:
            row["coldkey_total_reg_cost_tao"] = round(totals[coldkey], 6)
        else:
            row["coldkey_total_reg_cost_tao"] = None


def _attach_emission_fields(rows: list[dict], subtensor, netuid: int) -> dict:
    """Attach per-UID and per-coldkey miner emission / daily TAO (validators excluded)."""
    metagraph = subtensor.metagraph(netuid=netuid, lite=True)
    subnet_info = subtensor.subnet(netuid=netuid)
    tempo = int(subnet_info.tempo or 360)
    alpha_price_tao = float(subnet_info.price or 0.0)

    uid_emission: dict[int, float] = {}
    uid_daily_tao: dict[int, float] = {}
    uid_is_validator: dict[int, bool] = {}
    for uid in range(metagraph.n):
        is_validator = _is_validator_uid(metagraph, uid)
        uid_is_validator[uid] = is_validator
        emission = _miner_emission(metagraph, uid)
        uid_emission[uid] = round(emission, 6)
        uid_daily_tao[uid] = _emission_to_daily_tao(
            emission,
            tempo=tempo,
            alpha_price_tao=alpha_price_tao,
        )

    coldkey_emission: dict[str, float] = defaultdict(float)
    coldkey_daily_tao: dict[str, float] = defaultdict(float)
    for row in rows:
        uid = row.get("uid")
        if uid is None:
            continue
        uid = int(uid)
        is_validator = uid_is_validator.get(uid, False)
        emission = uid_emission.get(uid, 0.0)
        daily_tao = uid_daily_tao.get(uid, 0.0)
        row["is_validator"] = is_validator
        row["emission"] = emission
        row["daily_tao"] = daily_tao
        if is_validator:
            continue
        coldkey = (row.get("coldkey") or "").strip()
        if coldkey:
            coldkey_emission[coldkey] += emission
            coldkey_daily_tao[coldkey] += daily_tao

    for row in rows:
        coldkey = (row.get("coldkey") or "").strip()
        if coldkey:
            row["coldkey_total_emission"] = round(coldkey_emission[coldkey], 6)
            row["coldkey_daily_tao"] = round(coldkey_daily_tao[coldkey], 6)
        else:
            row["coldkey_total_emission"] = None
            row["coldkey_daily_tao"] = None

    validator_count = sum(1 for value in uid_is_validator.values() if value)
    total_emission = sum(uid_emission.values())
    total_daily_tao = sum(uid_daily_tao.values())
    return {
        "total_emission": round(total_emission, 6),
        "total_daily_tao": round(total_daily_tao, 6),
        "validator_count": validator_count,
        "miners_only": True,
        "tempo": tempo,
        "tempos_per_day": round(_tempos_per_day(tempo), 2),
        "alpha_price_tao": round(alpha_price_tao, 9),
    }


def _registration_day(registered_at: str | None) -> str | None:
    if not registered_at:
        return None
    day = str(registered_at).strip()[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        return day
    return None


def _build_daily_registration_spend(rows: list[dict]) -> dict:
    """Aggregate registration burn (TAO) by calendar day (UTC; browsers re-group locally)."""
    by_day: dict[str, dict] = {}
    skipped_missing_cost = 0
    skipped_missing_date = 0
    for row in rows:
        cost = row.get("registration_cost_tao")
        if cost is None:
            skipped_missing_cost += 1
            continue
        day = _registration_day(row.get("registered_at"))
        if not day:
            skipped_missing_date += 1
            continue
        bucket = by_day.setdefault(
            day,
            {
                "date": day,
                "registrations": 0,
                "total_tao": 0.0,
                "team_tao": 0.0,
                "team_registrations": 0,
            },
        )
        bucket["registrations"] += 1
        bucket["total_tao"] += float(cost)
        if row.get("is_team"):
            bucket["team_tao"] += float(cost)
            bucket["team_registrations"] += 1

    recent_days = []
    for day in sorted(by_day, reverse=True):
        item = by_day[day]
        recent_days.append(
            {
                **item,
                "total_tao": round(item["total_tao"], 6),
                "team_tao": round(item["team_tao"], 6),
            }
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_bucket = by_day.get(today)
    today_stats = {
        "date": today,
        "registrations": today_bucket["registrations"] if today_bucket else 0,
        "total_tao": round(today_bucket["total_tao"], 6) if today_bucket else 0.0,
        "team_tao": round(today_bucket["team_tao"], 6) if today_bucket else 0.0,
        "team_registrations": today_bucket["team_registrations"] if today_bucket else 0,
    }
    return {
        "timezone": "UTC",
        "today": today_stats,
        "recent_days": recent_days,
        "skipped_missing_cost": skipped_missing_cost,
        "skipped_missing_date": skipped_missing_date,
    }


def _fetch_tmc_neurons(netuid: int) -> list[dict]:
    """Fetch registered neurons from TaoMarketCap public API."""
    api_key = os.getenv("TMC_API_KEY", "").strip() or None
    headers = {"Accept": "application/json", "User-Agent": "oro-sn15-monitor/1.0"}
    if api_key:
        headers["Authorization"] = api_key
    url = TMC_NEURONS_API.format(netuid=netuid)
    try:
        payload = _get_json(url, headers=headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("TaoMarketCap API rate limited (429)") from exc
        raise RuntimeError(f"TaoMarketCap API HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"TaoMarketCap API unreachable: {exc.reason}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected TaoMarketCap neurons payload")
    return payload


def _build_rows_from_tmc_neurons(neurons: list[dict]) -> tuple[list[dict], dict[str, int], dict, dict]:
    coldkey_counts: dict[str, int] = defaultdict(int)
    for neuron in neurons:
        coldkey = (neuron.get("owner") or "").strip()
        if coldkey:
            coldkey_counts[coldkey] += 1

    rows: list[dict] = []
    for neuron in neurons:
        coldkey = (neuron.get("owner") or "").strip()
        hotkey = (neuron.get("hotkey") or "").strip()
        if not hotkey:
            continue
        reg_cost = _tao_from_rao(neuron.get("registration_price"))
        rows.append(
            _annotate_team_fields(
                {
                    "uid": neuron.get("uid"),
                    "hotkey": hotkey,
                    "coldkey": coldkey,
                    "registered_at": _format_registered_at(neuron.get("registration_block_time")),
                    "registration_cost_tao": reg_cost,
                    "coldkey_uid_count": coldkey_counts.get(coldkey, 0),
                }
            )
        )

    rows.sort(
        key=lambda row: (
            row["registered_at"] is None,
            row["registered_at"] or "",
            row["uid"] if row["uid"] is not None else -1,
        ),
        reverse=True,
    )

    registration_times_meta = {
        "source": "taomarketcap",
        "cached_blocks": sum(1 for row in rows if row.get("registered_at")),
        "total_blocks": len(rows),
        "note": "Registration times via TaoMarketCap public API.",
    }
    registration_cost_meta = {
        "source": "taomarketcap",
        "matched": sum(1 for row in rows if row.get("registration_cost_tao") is not None),
        "target_hotkeys": len(rows),
        "note": "Registration burn via TaoMarketCap public API.",
    }
    return rows, dict(coldkey_counts), registration_times_meta, registration_cost_meta


def _format_registered_at(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return text


def _load_block_time_cache() -> dict[int, str]:
    if not BLOCK_TIME_CACHE_PATH.is_file():
        return {}
    try:
        raw = json.loads(BLOCK_TIME_CACHE_PATH.read_text())
    except Exception:
        return {}
    cache: dict[int, str] = {}
    for key, value in raw.items():
        try:
            cache[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return cache


def _save_block_time_cache(cache: dict[int, str]) -> None:
    BLOCK_TIME_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {str(block): timestamp for block, timestamp in sorted(cache.items())}
    BLOCK_TIME_CACHE_PATH.write_text(json.dumps(payload, indent=2))


def _resolve_block_timestamps(
    blocks: set[int],
    *,
    max_lookups: int = 30,
) -> tuple[dict[int, str], dict]:
    """Map registration block numbers to UTC timestamps via persistent cache."""
    import bittensor as bt

    with _block_time_cache_lock:
        cache = _load_block_time_cache()

    missing = sorted(block for block in blocks if block not in cache)
    looked_up = 0
    errors = 0
    finney_st = bt.Subtensor(network="finney")
    archive_st = None

    for block in missing[:max_lookups]:
        timestamp = None
        try:
            timestamp = finney_st.get_timestamp(block=block)
        except Exception:
            if archive_st is None:
                try:
                    archive_st = bt.Subtensor(network="finney", archive_endpoints=[ARCHIVE_ENDPOINT])
                except Exception:
                    pass
            if archive_st is not None:
                try:
                    timestamp = archive_st.get_timestamp(block=block)
                except Exception:
                    errors += 1
                    continue
            else:
                errors += 1
                continue

        cache[block] = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        looked_up += 1

    if looked_up:
        with _block_time_cache_lock:
            _save_block_time_cache(cache)

    cached_blocks = sum(1 for block in blocks if block in cache)
    remaining = len(missing) - looked_up
    meta = {
        "source": "chain_block_at_registration",
        "cached_blocks": cached_blocks,
        "total_blocks": len(blocks),
        "looked_up_this_fetch": looked_up,
        "remaining": remaining,
        "errors": errors,
    }
    if remaining:
        meta["note"] = (
            f"Registration times from on-chain blocks "
            f"({cached_blocks}/{len(blocks)} cached; {remaining} still warming)."
        )
    else:
        meta["note"] = f"All {len(blocks)} registration times loaded from chain blocks."
    return cache, meta


def warm_block_time_cache(netuid: int = NETUID, *, max_lookups: int = 9999) -> dict:
    """Pre-populate the block timestamp cache for all SN15 registration blocks."""
    import bittensor as bt

    metagraph = bt.Subtensor(network="finney").metagraph(netuid=netuid, lite=True)
    blocks = {
        int(metagraph.block_at_registration[uid])
        for uid in range(metagraph.n)
        if uid < len(metagraph.block_at_registration) and metagraph.block_at_registration[uid]
    }
    _, meta = _resolve_block_timestamps(blocks, max_lookups=max_lookups)
    return meta


def _removal_sort_key(row: dict) -> tuple:
    return (
        float(row.get("emission") or 0),
        int(row.get("registration_block") or 0),
        int(row.get("uid") if row.get("uid") is not None else -1),
    )


def _build_to_be_removed_from_tmc_neurons(neurons: list[dict], current_block: int) -> list[dict]:
    immunity_period = int(neurons[0].get("immunity_period") or 5000) if neurons else 5000
    rows: list[dict] = []
    for neuron in neurons:
        hotkey = (neuron.get("hotkey") or "").strip()
        coldkey = (neuron.get("owner") or "").strip()
        if not hotkey:
            continue
        reg_block = int(neuron.get("block_at_registration") or 0)
        if reg_block + immunity_period > current_block:
            continue
        rows.append(
            _annotate_team_fields(
                {
                    "uid": neuron.get("uid"),
                    "hotkey": hotkey,
                    "coldkey": coldkey,
                    "emission": round(float(neuron.get("emission") or 0), 6),
                    "registered_at": _format_registered_at(neuron.get("registration_block_time")),
                    "registration_block": reg_block,
                    "registration_cost_tao": _tao_from_rao(neuron.get("registration_price")),
                }
            )
        )
    rows.sort(key=_removal_sort_key)
    for rank, row in enumerate(rows, start=1):
        row["remove_rank"] = rank
    return rows


def _build_to_be_removed_from_metagraph(subtensor, netuid: int) -> tuple[list[dict], int, int]:
    metagraph = subtensor.metagraph(netuid=netuid, lite=True)
    current_block = int(subtensor.block)
    immunity_period = int(subtensor.immunity_period(netuid=netuid))
    rows: list[dict] = []
    for uid in range(metagraph.n):
        hotkey = metagraph.hotkeys[uid]
        if not hotkey:
            continue
        reg_block = int(metagraph.block_at_registration[uid])
        if reg_block + immunity_period > current_block:
            continue
        rows.append(
            _annotate_team_fields(
                {
                    "uid": uid,
                    "hotkey": hotkey,
                    "coldkey": metagraph.coldkeys[uid],
                    "emission": round(float(metagraph.emission[uid]), 6),
                    "registered_at": None,
                    "registration_block": reg_block,
                    "registration_cost_tao": None,
                }
            )
        )
    rows.sort(key=_removal_sort_key)
    for rank, row in enumerate(rows, start=1):
        row["remove_rank"] = rank
    return rows, current_block, immunity_period


def fetch_to_be_removed_rows(netuid: int = NETUID) -> dict:
    """Return non-immune neurons ranked by deregistration risk (TaoMarketCap-style)."""
    import bittensor as bt

    source = "bittensor"
    current_block: int | None = None
    immunity_period = 5000
    fallback_note: str | None = None
    rows: list[dict] = []

    try:
        neurons = _fetch_tmc_neurons(netuid)
        current_block = int(neurons[0]["block_number"])
        immunity_period = int(neurons[0].get("immunity_period") or 5000)
        rows = _build_to_be_removed_from_tmc_neurons(neurons, current_block)
        source = "taomarketcap"
    except Exception as exc:
        fallback_note = str(exc)

    if not rows:
        subtensor = bt.Subtensor(network="finney")
        rows, current_block, immunity_period = _build_to_be_removed_from_metagraph(subtensor, netuid)
        if fallback_note:
            source = "bittensor_fallback"

    team_rows = [row for row in rows if row.get("is_team")]
    next_remove = rows[0] if rows else None
    next_team = next((row for row in rows if row.get("is_team")), None)

    note = (
        "Ranked by lowest emission, then earliest registration block, then UID "
        "(same order as taomarketcap.com/subnets/15/registration → To be Removed)."
    )
    if fallback_note and source == "bittensor_fallback":
        note = f"TaoMarketCap unavailable ({fallback_note}); using on-chain metagraph. {note}"

    return {
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "team_rows": team_rows,
        "next_to_remove": next_remove,
        "next_team_at_risk": next_team,
        "summary": {
            "total_eligible": len(rows),
            "team_at_risk": len(team_rows),
            "best_team_remove_rank": team_rows[0]["remove_rank"] if team_rows else None,
            "next_remove_uid": next_remove.get("uid") if next_remove else None,
            "next_remove_is_team": bool(next_remove and next_remove.get("is_team")),
            "immunity_period_blocks": immunity_period,
            "current_block": current_block,
        },
        "note": note,
    }


def fetch_registration_monitor_rows(
    netuid: int = NETUID,
    *,
    include_registration_times: bool = True,
    max_block_lookups: int = 30,
) -> dict:
    """Return flat registration rows for the SN15 register monitor dashboard."""
    import bittensor as bt

    subtensor = bt.Subtensor(network="finney")
    current_registration_cost_tao = _current_registration_cost_tao(subtensor, netuid)

    registration_times_meta = {
        "source": "none",
        "cached_blocks": 0,
        "total_blocks": 0,
        "note": "Registration times unavailable.",
    }
    registration_cost_meta = {
        "source": "none",
        "matched": 0,
        "target_hotkeys": 0,
        "note": "Registration cost unavailable.",
    }
    rows: list[dict] = []
    total_slots = 256

    try:
        neurons = _fetch_tmc_neurons(netuid)
        rows, coldkey_counts, registration_times_meta, registration_cost_meta = _build_rows_from_tmc_neurons(
            neurons
        )
        total_slots = max(total_slots, len(rows))
    except Exception as exc:
        registration_times_meta["note"] = f"TaoMarketCap unavailable ({exc}); falling back to chain."
        registration_cost_meta["note"] = registration_times_meta["note"]

        metagraph = subtensor.metagraph(netuid=netuid, lite=True)
        total_slots = int(metagraph.n)
        block_at_reg = list(metagraph.block_at_registration)

        by_coldkey: dict[str, list[dict]] = defaultdict(list)
        for uid in range(metagraph.n):
            hotkey = metagraph.hotkeys[uid]
            coldkey = metagraph.coldkeys[uid]
            if not hotkey:
                continue
            by_coldkey[coldkey].append({"uid": uid, "hotkey": hotkey})

        coldkey_counts = {coldkey: len(miners) for coldkey, miners in by_coldkey.items()}
        target_hotkeys = {miner["hotkey"] for miners in by_coldkey.values() for miner in miners}
        registration_cost_meta["target_hotkeys"] = len(target_hotkeys)

        api_key = os.getenv("TAOSTATS_API_KEY", "").strip() or None
        reg_costs: dict[str, dict] = {}
        if api_key:
            try:
                reg_costs, registration_cost_meta = _fetch_taostats_registration_costs(
                    netuid,
                    api_key,
                    target_hotkeys,
                )
                registration_cost_meta["source"] = "taostats"
                registration_cost_meta["note"] = "Registration burn via Taostats API."
            except Exception as taostats_exc:
                registration_cost_meta["source"] = "taostats_error"
                registration_cost_meta["note"] = str(taostats_exc)

        block_times: dict[int, str] = {}
        if include_registration_times:
            unique_blocks = {
                int(block_at_reg[uid])
                for uid in range(metagraph.n)
                if uid < len(block_at_reg) and block_at_reg[uid]
            }
            block_times, registration_times_meta = _resolve_block_timestamps(
                unique_blocks,
                max_lookups=max_block_lookups,
            )

        for coldkey, miners in by_coldkey.items():
            uid_count = coldkey_counts[coldkey]
            for miner in miners:
                uid = miner["uid"]
                hotkey = miner["hotkey"]
                info = reg_costs.get(hotkey, {})
                registered_at = _format_registered_at(info.get("registered_at"))
                if not registered_at and uid < len(block_at_reg) and block_at_reg[uid]:
                    registered_at = block_times.get(int(block_at_reg[uid]))
                rows.append(
                    _annotate_team_fields(
                        {
                            "uid": uid,
                            "hotkey": hotkey,
                            "coldkey": coldkey,
                            "registered_at": registered_at,
                            "registration_cost_tao": info.get("registration_cost_tao"),
                            "coldkey_uid_count": uid_count,
                        }
                    )
                )

        rows.sort(
            key=lambda row: (
                row["registered_at"] is None,
                row["registered_at"] or "",
                row["uid"],
            ),
            reverse=True,
        )

    _attach_coldkey_reg_totals(rows)
    emission_meta = _attach_emission_fields(rows, subtensor, netuid)

    total_registered = len(rows)
    unique_coldkeys = len(coldkey_counts)
    multi_uid_coldkeys = sum(1 for count in coldkey_counts.values() if count > 1)
    known_costs = sum(1 for row in rows if row.get("registration_cost_tao") is not None)
    known_times = sum(1 for row in rows if row.get("registered_at"))
    team_rows = [row for row in rows if row.get("is_team")]
    team_registered_members = len({row["coldkey"] for row in team_rows})
    max_coldkey_uid_count = max(coldkey_counts.values()) if coldkey_counts else 0
    team_total_emission = round(sum(row.get("emission") or 0 for row in team_rows), 6)
    team_daily_tao = round(sum(row.get("daily_tao") or 0 for row in team_rows), 6)
    total_daily_tao = emission_meta["total_daily_tao"]
    team_daily_tao_pct = round(100 * team_daily_tao / total_daily_tao, 2) if total_daily_tao else 0.0
    daily_registration_spend = _build_daily_registration_spend(rows)
    today_reg = daily_registration_spend["today"]

    return {
        "netuid": netuid,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "team_coldkeys": TEAM_COLDKEYS,
        "registration_costs": registration_cost_meta,
        "registration_times": registration_times_meta,
        "emission": emission_meta,
        "daily_registration_spend": daily_registration_spend,
        "summary": {
            "total_slots": total_slots,
            "registered_count": total_registered,
            "unique_coldkeys": unique_coldkeys,
            "multi_uid_coldkeys": multi_uid_coldkeys,
            "max_coldkey_uid_count": max_coldkey_uid_count,
            "current_registration_cost_tao": current_registration_cost_tao,
            "known_registration_costs": known_costs,
            "known_registration_times": known_times,
            "team_members": len(TEAM_COLDKEYS),
            "team_registered_members": team_registered_members,
            "team_uids": len(team_rows),
            "total_emission": emission_meta["total_emission"],
            "total_daily_tao": total_daily_tao,
            "team_total_emission": team_total_emission,
            "team_daily_tao": team_daily_tao,
            "team_daily_tao_pct": team_daily_tao_pct,
            "validator_count": emission_meta.get("validator_count", 0),
            "today_registration_spend_tao": today_reg["total_tao"],
            "today_registration_count": today_reg["registrations"],
            "team_today_registration_spend_tao": today_reg["team_tao"],
            "team_today_registration_count": today_reg["team_registrations"],
        },
        "rows": rows,
    }
