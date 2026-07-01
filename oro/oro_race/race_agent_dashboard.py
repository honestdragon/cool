#!/usr/bin/env python3
"""Race agent problem-solving dashboard.

Live dashboard showing per-race agent statistics and per-problem solving status.
Layout: left filters, center tabbed tables, right detail panel.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Timer

from flask import Flask, jsonify, render_template_string, request

from race_agent_db import (
    DEFAULT_DB as RACE_AGENT_DB,
    get_cached_race_payload,
    is_cacheable_status,
    list_cached_races,
    save_race_payload,
)
from subnet_registration import TEAM_COLDKEYS, fetch_registration_monitor_rows

ORO_RACE = Path(__file__).resolve().parent
ORO_ROOT = ORO_RACE.parent
ENV_PATHS = [
    ORO_ROOT / ".env",
    ORO_ROOT / "reg_bot" / "config.env",
]
STATIC_DIR = ORO_RACE / "static"
DEFAULT_CSV = ORO_RACE / "race-problems-queries-2026-06-22.csv"
QUERY_MAP_PATH = ORO_ROOT / "data" / "races" / "race_problem_query_map.json"

BASE_URL = "https://api.oroagents.com"
HISTORY_RACE_LIMIT = 10

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

_rate_lock = threading.Lock()
_last_request_at = 0.0

_race_list_cache: dict | None = None
_race_list_cache_at: float = 0.0
_payload_cache: dict[int, dict] = {}
_payload_cache_at: dict[int, float] = {}
_cache_lock = threading.Lock()

_reg_cache: dict | None = None
_reg_cache_at: float = 0.0
_reg_lock = threading.Lock()

_agent_history_cache: dict | None = None
_agent_history_cache_at: float = 0.0
_agent_history_lock = threading.Lock()

CSV_INDEX: dict[tuple[int, str], dict] = {}
QUERY_MAP: dict[str, dict[str, str]] = {}


def load_env() -> None:
    import os

    for path in ENV_PATHS:
        if not path.is_file():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def get_registration_payload(*, force: bool = False, cache_seconds: float = 90.0) -> dict:
    global _reg_cache, _reg_cache_at
    now = time.monotonic()
    with _reg_lock:
        if not force and _reg_cache is not None and (now - _reg_cache_at) < cache_seconds:
            return _reg_cache
    payload = fetch_registration_monitor_rows(
        include_registration_times=False,
        max_block_lookups=0,
    )
    with _reg_lock:
        _reg_cache = payload
        _reg_cache_at = now
    return payload


def attach_team_context(payload: dict, reg_payload: dict | None) -> dict:
    hotkey_map = {
        row["hotkey"]: row for row in (reg_payload or {}).get("rows") or [] if row.get("hotkey")
    }
    for agent in payload.get("agents") or []:
        row = hotkey_map.get(agent.get("miner_hotkey") or "")
        agent["is_team"] = bool(row and row.get("is_team"))
        agent["team_name"] = row.get("team_name") if row else None
        agent["miner_coldkey"] = row.get("coldkey") if row else None
        agent["uid"] = row.get("uid") if row else None

    team_agents = [agent for agent in payload.get("agents") or [] if agent.get("is_team")]
    ranked = [agent for agent in team_agents if agent.get("race_rank") is not None]
    best = min(ranked, key=lambda item: item["race_rank"]) if ranked else None
    reg_summary = (reg_payload or {}).get("summary") or {}
    payload["team"] = {
        "team_coldkeys": (reg_payload or {}).get("team_coldkeys") or TEAM_COLDKEYS,
        "registration_summary": reg_summary,
        "members_in_race": len(team_agents),
        "members_registered_on_subnet": reg_summary.get("team_registered_members"),
        "team_uids_on_subnet": reg_summary.get("team_uids"),
        "team_daily_tao": reg_summary.get("team_daily_tao"),
        "team_daily_tao_pct": reg_summary.get("team_daily_tao_pct"),
        "agents": team_agents,
        "best_rank": best.get("race_rank") if best else None,
        "best_agent_name": best.get("agent_name") if best else None,
        "best_race_score": best.get("race_score") if best else None,
        "best_both_pass_rate": best.get("both_pass_rate") if best else None,
    }
    return payload


def slice_race_payload(payload: dict, top_agents: int | None) -> dict:
    agents = payload.get("agents") or []
    if top_agents is None or len(agents) <= top_agents:
        sliced = dict(payload)
        sliced["top_agents_limit"] = len(agents) if top_agents is None else top_agents
        summary = dict(sliced.get("summary") or {})
        summary["agent_count_loaded"] = len(agents)
        sliced["summary"] = summary
        return sliced

    selected = agents[:top_agents]
    agent_ids = {agent["agent_version_id"] for agent in selected}
    problems: list[dict] = []
    for prob in payload.get("problems") or []:
        by_agent = {
            aid: cell for aid, cell in (prob.get("by_agent") or {}).items() if aid in agent_ids
        }
        solvers = [
            agent["agent_name"]
            for agent in selected
            if by_agent.get(agent["agent_version_id"], {}).get("status") == "SUCCESS"
        ]
        problems.append(
            {
                **prob,
                "by_agent": by_agent,
                "solver_count": len(solvers),
                "solver_agents": solvers[:8],
                "solver_agents_all": solvers,
            }
        )

    categories = ("Product", "Shop", "Voucher")
    category_stats = {cat: {"success": 0, "failed": 0, "pending": 0, "agents": 0} for cat in categories}
    for row in selected:
        for cat, bucket in (row.get("by_category") or {}).items():
            if cat not in category_stats:
                category_stats[cat] = {"success": 0, "failed": 0, "pending": 0, "agents": 0}
            category_stats[cat]["success"] += bucket.get("success", 0)
            category_stats[cat]["failed"] += bucket.get("failed", 0)
            category_stats[cat]["pending"] += bucket.get("pending", 0)
            category_stats[cat]["agents"] += 1

    summary = dict(payload.get("summary") or {})
    summary.update(
        {
            "agent_count_loaded": len(selected),
            "total_success": sum(row.get("success_count", 0) for row in selected),
            "total_failed": sum(row.get("failed_count", 0) for row in selected),
            "unique_solvers": len(
                {
                    name
                    for prob in problems
                    for name in prob.get("solver_agents_all") or []
                }
            ),
        }
    )

    sliced = dict(payload)
    sliced["agents"] = selected
    sliced["problems"] = problems
    sliced["category_stats"] = category_stats
    sliced["summary"] = summary
    sliced["top_agents_limit"] = top_agents
    return sliced


def _throttle(min_interval: float = 0.06) -> None:
    global _last_request_at
    with _rate_lock:
        now = time.monotonic()
        wait = min_interval - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def get_json(url: str, timeout: int = 60) -> dict | list:
    _throttle()
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 1.0
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 502, 503, 504) and attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
        except urllib.error.URLError:
            if attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise


def load_csv_index() -> None:
    global CSV_INDEX
    if not DEFAULT_CSV.is_file():
        return
    import csv

    index: dict[tuple[int, str], dict] = {}
    with DEFAULT_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                race_number = int(row.get("race_number") or 0)
            except ValueError:
                continue
            query = (row.get("query") or "").strip()
            if not query:
                continue
            index[(race_number, query)] = row
    CSV_INDEX = index


def load_query_map() -> None:
    global QUERY_MAP
    if QUERY_MAP_PATH.is_file():
        QUERY_MAP = json.loads(QUERY_MAP_PATH.read_text(encoding="utf-8"))


def extract_validators(problem: dict) -> list[dict]:
    """Return up to two validators in stable hotkey order (V1, V2)."""
    vrs = sorted(
        problem.get("validator_results") or [],
        key=lambda item: item.get("validator_hotkey") or "",
    )
    validators: list[dict] = []
    for index, vr in enumerate(vrs[:2], start=1):
        hotkey = vr.get("validator_hotkey") or ""
        exec_s = vr.get("execution_time")
        validators.append(
            {
                "index": index,
                "label": f"V{index}",
                "validator_hotkey": hotkey,
                "validator_short": hotkey[-8:] if hotkey else "?",
                "status": vr.get("status") or "PENDING",
                "score": vr.get("score"),
                "reasoning_score": vr.get("reasoning_score"),
                "exec_s": round(float(exec_s), 1) if exec_s else None,
                "inference_failures": int(vr.get("inference_failure_count") or 0),
            }
        )
    while len(validators) < 2:
        validators.append(
            {
                "index": len(validators) + 1,
                "label": f"V{len(validators) + 1}",
                "validator_hotkey": "",
                "validator_short": "?",
                "status": "MISSING",
                "score": None,
                "reasoning_score": None,
                "exec_s": None,
                "inference_failures": 0,
            }
        )
    return validators


def discover_race_validators(
    agent_problem_maps: dict[str, dict[str, dict]],
    problem_ids: list[str],
    ref_id: str | None,
) -> list[dict]:
    if ref_id:
        for pid in problem_ids:
            problem = agent_problem_maps.get(ref_id, {}).get(pid)
            if problem and problem.get("validator_results"):
                return [
                    {
                        "label": item["label"],
                        "validator_short": item["validator_short"],
                        "validator_hotkey": item["validator_hotkey"],
                    }
                    for item in extract_validators(problem)
                ]
    for prob_map in agent_problem_maps.values():
        for problem in prob_map.values():
            if problem.get("validator_results"):
                return [
                    {
                        "label": item["label"],
                        "validator_short": item["validator_short"],
                        "validator_hotkey": item["validator_hotkey"],
                    }
                    for item in extract_validators(problem)
                ]
    return [
        {"label": "V1", "validator_short": "?", "validator_hotkey": ""},
        {"label": "V2", "validator_short": "?", "validator_hotkey": ""},
    ]


def problem_status(problem: dict) -> str:
    vrs = problem.get("validator_results") or []
    if not vrs:
        return "PENDING"
    if any(vr.get("status") == "SUCCESS" for vr in vrs):
        return "SUCCESS"
    if all(vr.get("status") == "FAILED" for vr in vrs):
        return "FAILED"
    return vrs[0].get("status") or "UNKNOWN"


def problem_metrics(problem: dict) -> dict:
    validators = extract_validators(problem)
    v1_status = validators[0]["status"]
    v2_status = validators[1]["status"]
    success_vrs = [vr for vr in validators if vr.get("status") == "SUCCESS"]
    exec_times = [float(vr.get("exec_s") or 0) for vr in validators if vr.get("exec_s")]
    scores = [float(vr.get("score") or 0) for vr in validators if vr.get("score") is not None]
    reasoning = [
        float(vr.get("reasoning_score") or 0)
        for vr in validators
        if vr.get("reasoning_score") is not None
    ]
    return {
        "status": problem_status(problem),
        "v1_status": v1_status,
        "v2_status": v2_status,
        "both_success": v1_status == "SUCCESS" and v2_status == "SUCCESS",
        "validators": validators,
        "validator_count": len(problem.get("validator_results") or []),
        "success_validators": len(success_vrs),
        "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
        "avg_reasoning": round(sum(reasoning) / len(reasoning), 4) if reasoning else None,
        "avg_exec_s": round(sum(exec_times) / len(exec_times), 1) if exec_times else None,
        "inference_failures": sum(int(vr.get("inference_failures") or 0) for vr in validators),
    }


def fetch_race_list(force: bool = False, cache_seconds: float = 60.0) -> list[dict]:
    global _race_list_cache, _race_list_cache_at
    now = time.monotonic()
    with _cache_lock:
        if not force and _race_list_cache is not None and (now - _race_list_cache_at) < cache_seconds:
            return _race_list_cache

    races: list[dict] = []
    try:
        current = get_json(f"{BASE_URL}/v1/public/races/current")
        if current.get("race"):
            races.append(_normalize_race_summary(current["race"]))
    except Exception:
        pass

    history = get_json(f"{BASE_URL}/v1/public/races/history?limit=100&offset=0")
    seen = {r["race_id"] for r in races}
    for item in history.get("races") or []:
        if item.get("race_id") not in seen:
            races.append(_normalize_race_summary(item))
            seen.add(item["race_id"])

    races.sort(key=lambda r: int(r.get("race_number") or 0), reverse=True)
    with _cache_lock:
        _race_list_cache = races
        _race_list_cache_at = now
    return races


def _normalize_race_summary(race: dict) -> dict:
    return {
        "race_id": race.get("race_id"),
        "race_number": race.get("race_number"),
        "status": race.get("status"),
        "winner_agent_name": race.get("winner_agent_name"),
        "winner_score": race.get("winner_score"),
        "qualifier_count": race.get("qualifier_count"),
        "qualifying_threshold": race.get("qualifying_threshold"),
        "race_started_at": race.get("race_started_at"),
        "race_completed_at": race.get("race_completed_at"),
    }


def resolve_race(race_number: int) -> tuple[dict, list[dict]]:
    races = fetch_race_list(force=True)
    for race in races:
        if int(race.get("race_number") or 0) == race_number:
            detail = get_json(f"{BASE_URL}/v1/public/races/{race['race_id']}")
            return detail.get("race") or race, detail.get("qualifiers") or []

    history = get_json(f"{BASE_URL}/v1/public/races/history?limit=100&offset=0")
    for item in history.get("races") or []:
        if int(item.get("race_number", 0)) == race_number:
            detail = get_json(f"{BASE_URL}/v1/public/races/{item['race_id']}")
            return detail.get("race") or item, detail.get("qualifiers") or []
    raise ValueError(f"Race {race_number} not found")


def race_agents(qualifiers: list[dict]) -> list[dict]:
    agents: list[dict] = []
    for q in qualifiers:
        score = q.get("race_score")
        if score is None:
            score = q.get("qualifying_score")
        agents.append(
            {
                "agent_name": q.get("agent_name") or "?",
                "agent_version_id": q.get("agent_version_id"),
                "race_score": float(score) if score is not None else None,
                "race_rank": q.get("race_rank"),
                "qualifying_score": q.get("qualifying_score"),
                "qualification_type": q.get("qualification_type"),
                "miner_hotkey": q.get("miner_hotkey"),
                "is_discarded": bool(q.get("is_discarded")),
            }
        )

    def sort_key(item: dict) -> tuple:
        score = item.get("race_score")
        rank = item.get("race_rank")
        return (
            0 if score is not None else 1,
            -(score or 0),
            rank if rank is not None else 9999,
            item["agent_name"],
        )

    agents.sort(key=sort_key)
    return agents


def fetch_agent_problems(race_id: str, agent_version_id: str) -> list[dict]:
    data = get_json(
        f"{BASE_URL}/v1/public/agent-versions/{agent_version_id}/problems"
        f"?phase=RACE&race_id={race_id}"
    )
    return data.get("problems") or []


def enrich_problem_meta(race_number: int, problem_id: str, category: str) -> dict:
    query = (QUERY_MAP.get(str(race_number)) or {}).get(problem_id) or ""
    csv_row = CSV_INDEX.get((race_number, query)) if query else None
    if not csv_row and query:
        for (_rn, q), row in CSV_INDEX.items():
            if _rn == race_number and q == query:
                csv_row = row
                break
    return {
        "query": query,
        "query_code": (csv_row or {}).get("query_code") or "",
        "correct_answer": (csv_row or {}).get("correct_answer") or "",
        "answer_agent": (csv_row or {}).get("answer_agent") or "",
        "frequency": (csv_row or {}).get("frequency") or "",
        "appeared_race_numbers": (csv_row or {}).get("appeared_race_numbers") or "",
        "category": category,
    }


def build_race_payload(
    race_number: int,
    *,
    top_agents: int = 40,
    max_workers: int = 8,
    fetch_all: bool = False,
) -> dict:
    race, qualifiers = resolve_race(race_number)
    race_id = race["race_id"]
    agents = race_agents(qualifiers)
    if fetch_all:
        scored = [
            agent
            for agent in agents
            if not agent.get("is_discarded") and agent.get("race_score") is not None
        ]
        selected = scored if scored else [agent for agent in agents if not agent.get("is_discarded")]
    else:
        selected = [agent for agent in agents if not agent.get("is_discarded")][:top_agents]

    agent_problem_maps: dict[str, dict[str, dict]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_agent_problems, race_id, agent["agent_version_id"]): agent
            for agent in selected
        }
        for future in as_completed(futures):
            agent = futures[future]
            try:
                probs = future.result()
            except Exception as exc:
                agent["fetch_error"] = str(exc)
                probs = []
            agent_problem_maps[agent["agent_version_id"]] = {p["problem_id"]: p for p in probs}

    ref_id = selected[0]["agent_version_id"] if selected else None
    problem_ids = list(agent_problem_maps.get(ref_id, {}).keys()) if ref_id else []
    if not problem_ids:
        for prob_map in agent_problem_maps.values():
            if prob_map:
                problem_ids = list(prob_map.keys())
                break

    categories = ("Product", "Shop", "Voucher")
    race_validators = discover_race_validators(agent_problem_maps, problem_ids, ref_id)
    agent_rows: list[dict] = []
    for agent in selected:
        prob_map = agent_problem_maps.get(agent["agent_version_id"], {})
        by_cat = {cat: {"success": 0, "failed": 0, "pending": 0, "total": 0} for cat in categories}
        success = failed = pending = 0
        v1_success = v2_success = both_success = split_success = both_failed = 0
        exec_samples: list[float] = []
        for pid in problem_ids:
            problem = prob_map.get(pid)
            if not problem:
                pending += 1
                continue
            metrics = problem_metrics(problem)
            cat = problem.get("category") or "Product"
            bucket = by_cat.setdefault(cat, {"success": 0, "failed": 0, "pending": 0, "total": 0})
            bucket["total"] += 1
            if metrics["status"] == "SUCCESS":
                success += 1
                bucket["success"] += 1
            elif metrics["status"] == "FAILED":
                failed += 1
                bucket["failed"] += 1
            else:
                pending += 1
                bucket["pending"] += 1
            if metrics["v1_status"] == "SUCCESS":
                v1_success += 1
            if metrics["v2_status"] == "SUCCESS":
                v2_success += 1
            if metrics["both_success"]:
                both_success += 1
            elif metrics["v1_status"] == "SUCCESS" or metrics["v2_status"] == "SUCCESS":
                split_success += 1
            elif metrics["v1_status"] == "FAILED" and metrics["v2_status"] == "FAILED":
                both_failed += 1
            if metrics["avg_exec_s"]:
                exec_samples.append(metrics["avg_exec_s"])

        total = len(problem_ids)
        agent_rows.append(
            {
                **agent,
                "problems_total": total,
                "success_count": success,
                "failed_count": failed,
                "pending_count": pending,
                "pass_rate": round(success / total, 4) if total else 0,
                "v1_success_count": v1_success,
                "v2_success_count": v2_success,
                "both_success_count": both_success,
                "split_success_count": split_success,
                "both_failed_count": both_failed,
                "v1_pass_rate": round(v1_success / total, 4) if total else 0,
                "v2_pass_rate": round(v2_success / total, 4) if total else 0,
                "both_pass_rate": round(both_success / total, 4) if total else 0,
                "avg_exec_s": round(sum(exec_samples) / len(exec_samples), 1) if exec_samples else None,
                "by_category": by_cat,
            }
        )

    problem_rows: list[dict] = []
    for pid in problem_ids:
        ref_problem = agent_problem_maps.get(ref_id, {}).get(pid) if ref_id else {}
        category = ref_problem.get("category") or "Product"
        meta = enrich_problem_meta(race_number, pid, category)
        by_agent: dict[str, dict] = {}
        solvers: list[str] = []
        for agent in selected:
            aid = agent["agent_version_id"]
            problem = agent_problem_maps.get(aid, {}).get(pid)
            if not problem:
                by_agent[aid] = {"status": "MISSING", "agent_name": agent["agent_name"]}
                continue
            metrics = problem_metrics(problem)
            by_agent[aid] = {"agent_name": agent["agent_name"], **metrics}
            if metrics["status"] == "SUCCESS":
                solvers.append(agent["agent_name"])

        problem_rows.append(
            {
                "problem_id": pid,
                **meta,
                "solver_count": len(solvers),
                "solver_agents": solvers[:8],
                "solver_agents_all": solvers,
                "by_agent": by_agent,
            }
        )

    category_stats = {cat: {"success": 0, "failed": 0, "pending": 0, "agents": 0} for cat in categories}
    for row in agent_rows:
        for cat, bucket in row["by_category"].items():
            if cat not in category_stats:
                category_stats[cat] = {"success": 0, "failed": 0, "pending": 0, "agents": 0}
            category_stats[cat]["success"] += bucket["success"]
            category_stats[cat]["failed"] += bucket["failed"]
            category_stats[cat]["pending"] += bucket["pending"]
            category_stats[cat]["agents"] += 1

    summary = {
        "agent_count_loaded": len(selected),
        "agent_count_total": len(agents),
        "problem_count": len(problem_ids),
        "total_success": sum(r["success_count"] for r in agent_rows),
        "total_failed": sum(r["failed_count"] for r in agent_rows),
        "unique_solvers": len({a for p in problem_rows for a in p["solver_agents_all"]}),
        "known_answers": sum(1 for p in problem_rows if p.get("correct_answer")),
    }

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "race": _normalize_race_summary(race),
        "race_validators": race_validators,
        "summary": summary,
        "agents": agent_rows,
        "problems": problem_rows,
        "category_stats": category_stats,
        "top_agents_limit": len(selected) if fetch_all else top_agents,
        "fetch_all": fetch_all,
    }


def parse_top_agents(raw: str | None) -> int | None:
    if raw is None:
        return 40
    if str(raw).strip().lower() == "all":
        return None
    return min(max(int(raw), 5), 500)


def get_cached_payload(
    race_number: int,
    *,
    top_agents: int | None,
    force: bool,
    cache_seconds: float,
) -> dict:
    now = time.monotonic()
    race_meta = next(
        (race for race in fetch_race_list() if int(race.get("race_number") or 0) == race_number),
        None,
    )
    race_status = (race_meta or {}).get("status")
    reg_payload = get_registration_payload(force=force)

    if not force and is_cacheable_status(race_status):
        db_payload = get_cached_race_payload(race_number, RACE_AGENT_DB)
        if db_payload:
            full_with_team = attach_team_context(dict(db_payload), reg_payload)
            payload = slice_race_payload(db_payload, top_agents)
            payload = attach_team_context(payload, reg_payload)
            payload["team"] = full_with_team["team"]
            payload["source"] = "database"
            payload["cached_at"] = db_payload.get("fetched_at")
            return payload

    if (
        not force
        and not is_cacheable_status(race_status)
    ):
        with _cache_lock:
            cached = _payload_cache.get(race_number)
            cached_at = _payload_cache_at.get(race_number, 0.0)
            if cached is not None and (now - cached_at) < cache_seconds:
                cached_limit = cached.get("top_agents_limit")
                if top_agents is None or (
                    cached_limit is not None
                    and (cached.get("fetch_all") or cached_limit >= (top_agents or 0))
                ):
                    return attach_team_context(slice_race_payload(dict(cached), top_agents), reg_payload)

    fetch_all = is_cacheable_status(race_status) or top_agents is None
    payload = build_race_payload(
        race_number,
        top_agents=top_agents if top_agents is not None else 40,
        fetch_all=fetch_all,
    )
    if fetch_all and is_cacheable_status(payload["race"].get("status")):
        save_race_payload(payload, RACE_AGENT_DB)
        full_with_team = attach_team_context(dict(payload), reg_payload)
        response = slice_race_payload(payload, top_agents)
        response = attach_team_context(response, reg_payload)
        response["team"] = full_with_team["team"]
        response["source"] = "api"
        response["cached_at"] = payload.get("fetched_at")
        return response

    full_payload = payload
    response = attach_team_context(slice_race_payload(full_payload, top_agents), reg_payload)
    response["source"] = "api"
    with _cache_lock:
        _payload_cache[race_number] = full_payload
        _payload_cache_at[race_number] = now
    return response


def participation_row_from_agent(agent: dict, race: dict, *, source: str) -> dict:
    return {
        "race_number": int(race.get("race_number") or 0),
        "race_id": race.get("race_id"),
        "race_status": race.get("status"),
        "race_rank": agent.get("race_rank"),
        "race_score": agent.get("race_score"),
        "agent_name": agent.get("agent_name") or "?",
        "agent_version_id": agent.get("agent_version_id"),
        "miner_hotkey": agent.get("miner_hotkey"),
        "is_discarded": bool(agent.get("is_discarded")),
        "is_winner": agent.get("race_rank") == 1,
        "both_pass_rate": agent.get("both_pass_rate"),
        "pass_rate": agent.get("pass_rate"),
        "problems_total": agent.get("problems_total"),
        "success_count": agent.get("success_count"),
        "both_success_count": agent.get("both_success_count"),
        "avg_exec_s": agent.get("avg_exec_s"),
        "qualification_type": agent.get("qualification_type"),
        "source": source,
    }


def participation_row_from_qualifier(qualifier: dict, race: dict) -> dict:
    score = qualifier.get("race_score")
    if score is None:
        score = qualifier.get("qualifying_score")
    return {
        "race_number": int(race.get("race_number") or 0),
        "race_id": race.get("race_id"),
        "race_status": race.get("status"),
        "race_rank": qualifier.get("race_rank"),
        "race_score": float(score) if score is not None else None,
        "agent_name": qualifier.get("agent_name") or "?",
        "agent_version_id": qualifier.get("agent_version_id"),
        "miner_hotkey": qualifier.get("miner_hotkey"),
        "is_discarded": bool(qualifier.get("is_discarded")),
        "is_winner": qualifier.get("race_rank") == 1,
        "both_pass_rate": None,
        "pass_rate": None,
        "problems_total": None,
        "success_count": None,
        "both_success_count": None,
        "avg_exec_s": None,
        "qualification_type": qualifier.get("qualification_type"),
        "source": "api",
    }


def _participation_sort_key(row: dict) -> tuple:
    rank = row.get("race_rank")
    score = row.get("race_score") or 0.0
    return (
        0 if rank is not None else 1,
        rank if rank is not None else 9999,
        -score,
    )


def _agent_history_key(row: dict) -> str:
    version_id = row.get("agent_version_id")
    if version_id:
        return f"vid:{version_id}"
    name = (row.get("agent_name") or "?").strip().lower()
    hotkey = row.get("miner_hotkey") or "?"
    return f"name:{name}|hk:{hotkey}"


def _counts_as_race_participation(row: dict) -> bool:
    """True when the agent actually raced, not merely qualified."""
    if row.get("is_discarded"):
        return False
    if row.get("race_rank") is not None:
        return True
    if (row.get("race_status") or "") == "RACE_COMPLETE":
        return row.get("race_score") is not None
    return False


def _slim_history_race_row(row: dict) -> dict:
    return {
        "race_number": row.get("race_number"),
        "race_status": row.get("race_status"),
        "race_rank": row.get("race_rank"),
        "race_score": row.get("race_score"),
        "both_pass_rate": row.get("both_pass_rate"),
        "pass_rate": row.get("pass_rate"),
        "problems_total": row.get("problems_total"),
        "both_success_count": row.get("both_success_count"),
        "is_winner": row.get("is_winner"),
    }


def build_agent_participation_history(
    *,
    force: bool = False,
    cache_seconds: float = 300.0,
    max_races: int = HISTORY_RACE_LIMIT,
) -> dict:
    global _agent_history_cache, _agent_history_cache_at
    now = time.monotonic()
    with _agent_history_lock:
        if (
            not force
            and _agent_history_cache is not None
            and (now - _agent_history_cache_at) < cache_seconds
        ):
            return _agent_history_cache

    cached_numbers = {item["race_number"] for item in list_cached_races(RACE_AGENT_DB)}
    races = fetch_race_list(force=force)[:max_races]
    participations: list[dict] = []
    races_included: list[dict] = []

    for race in races:
        race_number = int(race.get("race_number") or 0)
        if not race_number:
            continue
        races_included.append(_normalize_race_summary(race))

        if race_number in cached_numbers:
            payload = get_cached_race_payload(race_number, RACE_AGENT_DB)
            if not payload:
                continue
            race_meta = payload.get("race") or race
            for agent in payload.get("agents") or []:
                if agent.get("is_discarded"):
                    continue
                participations.append(
                    participation_row_from_agent(agent, race_meta, source="database")
                )
            continue

        try:
            detail = get_json(f"{BASE_URL}/v1/public/races/{race['race_id']}")
        except Exception:
            continue
        race_meta = detail.get("race") or race
        for qualifier in detail.get("qualifiers") or []:
            if qualifier.get("is_discarded"):
                continue
            participations.append(participation_row_from_qualifier(qualifier, race_meta))

    deduped: dict[tuple[str, int], dict] = {}
    for row in participations:
        dedupe_key = (_agent_history_key(row), int(row.get("race_number") or 0))
        existing = deduped.get(dedupe_key)
        if existing is None or _participation_sort_key(row) < _participation_sort_key(existing):
            deduped[dedupe_key] = row
    participations = list(deduped.values())
    participations = [row for row in participations if _counts_as_race_participation(row)]

    by_agent: dict[str, dict] = {}
    for row in participations:
        key = _agent_history_key(row)
        bucket = by_agent.setdefault(
            key,
            {
                "agent_name": row.get("agent_name") or "?",
                "agent_version_id": row.get("agent_version_id"),
                "miner_hotkey": row.get("miner_hotkey"),
                "races": [],
                "race_count": 0,
                "wins": 0,
                "best_rank": None,
                "avg_rank": None,
                "avg_score": None,
                "avg_both_pass_rate": None,
            },
        )
        bucket["races"].append(row)
        bucket["race_count"] += 1
        if row.get("is_winner"):
            bucket["wins"] += 1
        rank = row.get("race_rank")
        if rank is not None:
            bucket["best_rank"] = rank if bucket["best_rank"] is None else min(bucket["best_rank"], rank)

    recent_race_numbers = sorted(
        {int(race.get("race_number") or 0) for race in races_included if race.get("race_number")},
        reverse=True,
    )

    agents: list[dict] = []
    for key, bucket in by_agent.items():
        bucket["history_id"] = bucket.get("agent_version_id") or key
        bucket["races"].sort(key=lambda item: item["race_number"], reverse=True)
        races_by_number = {int(row["race_number"]): row for row in bucket["races"]}
        ranks = [row["race_rank"] for row in bucket["races"] if row.get("race_rank") is not None]
        scores = [row["race_score"] for row in bucket["races"] if row.get("race_score") is not None]
        both_rates = [
            row["both_pass_rate"] for row in bucket["races"] if row.get("both_pass_rate") is not None
        ]
        bucket["avg_rank"] = round(sum(ranks) / len(ranks), 1) if ranks else None
        bucket["avg_score"] = round(sum(scores) / len(scores), 4) if scores else None
        bucket["avg_both_pass_rate"] = round(sum(both_rates) / len(both_rates), 4) if both_rates else None
        bucket["latest_race"] = bucket["races"][0]["race_number"] if bucket["races"] else None
        for race_number in recent_race_numbers:
            entry = races_by_number.get(race_number)
            bucket[f"race_{race_number}_rank"] = entry.get("race_rank") if entry else None
            bucket[f"race_{race_number}_score"] = entry.get("race_score") if entry else None
        bucket["races"] = [_slim_history_race_row(row) for row in bucket["races"]]
        agents.append(bucket)

    agents.sort(
        key=lambda item: (
            -(item.get("race_count") or 0),
            item.get("best_rank") if item.get("best_rank") is not None else 9999,
            item.get("agent_name") or "",
        )
    )

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "race_count": len(races_included),
        "recent_race_limit": max_races,
        "recent_race_numbers": recent_race_numbers,
        "participation_count": len(participations),
        "cached_race_count": len(cached_numbers & {r["race_number"] for r in races_included}),
        "agents": agents,
    }
    with _agent_history_lock:
        _agent_history_cache = result
        _agent_history_cache_at = now
    return result


def build_team_rank_distribution(agents: list[dict], races: list[dict]) -> dict:
    race_numbers = sorted(
        {int(race.get("race_number") or 0) for race in races if race.get("race_number")},
    )
    team_agents = [agent for agent in agents if agent.get("is_team")]

    by_member: dict[str, dict] = {}
    for agent in team_agents:
        team_name = agent.get("team_name") or "?"
        bucket = by_member.setdefault(
            team_name,
            {
                "team_name": team_name,
                "agent_names": set(),
                "best_by_race": {},
            },
        )
        if agent.get("agent_name"):
            bucket["agent_names"].add(agent["agent_name"])
        for row in agent.get("races") or []:
            race_number = int(row.get("race_number") or 0)
            if not race_number:
                continue
            rank = row.get("race_rank")
            if rank is None:
                continue
            existing = bucket["best_by_race"].get(race_number)
            if existing is None or rank < existing["rank"]:
                bucket["best_by_race"][race_number] = {
                    "rank": rank,
                    "score": row.get("race_score"),
                    "agent_name": agent.get("agent_name"),
                }

    members: list[dict] = []
    for team_name in sorted(by_member.keys()):
        bucket = by_member[team_name]
        best_by_race = bucket["best_by_race"]
        if not best_by_race:
            continue
        ranks = [best_by_race.get(rn, {}).get("rank") for rn in race_numbers]
        scores = [best_by_race.get(rn, {}).get("score") for rn in race_numbers]
        agents_used = sorted(bucket["agent_names"])
        members.append(
            {
                "team_name": team_name,
                "agent_name": agents_used[0] if len(agents_used) == 1 else None,
                "agent_names": agents_used,
                "label": team_name,
                "ranks": ranks,
                "scores": scores,
                "agents_by_race": [
                    best_by_race.get(rn, {}).get("agent_name") for rn in race_numbers
                ],
                "best_rank": min(
                    (entry["rank"] for entry in best_by_race.values()),
                    default=None,
                ),
            }
        )

    members.sort(
        key=lambda item: (
            item.get("best_rank") if item.get("best_rank") is not None else 9999,
            item.get("team_name") or "",
        )
    )

    by_race: list[dict] = []
    for race_number in reversed(race_numbers):
        entries: list[dict] = []
        for member in members:
            idx = race_numbers.index(race_number)
            rank = member["ranks"][idx]
            if rank is None:
                continue
            entries.append(
                {
                    "team_name": member["team_name"],
                    "agent_name": by_member[member["team_name"]]["best_by_race"]
                    .get(race_number, {})
                    .get("agent_name"),
                    "label": member["label"],
                    "rank": rank,
                    "score": member["scores"][idx],
                }
            )
        entries.sort(key=lambda item: item["rank"])
        by_race.append({"race_number": race_number, "members": entries})

    return {
        "race_numbers": race_numbers,
        "member_count": len(members),
        "members": members,
        "by_race": by_race,
    }


def warm_completed_race_cache(*, limit: int | None = None, skip_existing: bool = True) -> list[int]:
    cached: list[int] = []
    races = fetch_race_list(force=True)
    complete = [race for race in races if is_cacheable_status(race.get("status"))]
    if limit is not None:
        complete = complete[:limit]
    for race in complete:
        race_number = int(race["race_number"])
        if skip_existing and get_cached_race_payload(race_number, RACE_AGENT_DB):
            continue
        print(f"Caching race {race_number}…", flush=True)
        payload = build_race_payload(race_number, fetch_all=True)
        save_race_payload(payload, RACE_AGENT_DB)
        cached.append(race_number)
    return cached


HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Race Agent Dashboard</title>
  <link href="/static/tabulator_midnight.min.css?v=1" rel="stylesheet">
  <script src="/static/tabulator.min.js?v=1"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b0f14;
      --panel: #121820;
      --panel-2: #1a2230;
      --border: #2a3444;
      --text: #e8edf5;
      --muted: #93a0b5;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --success: #22c55e;
      --failed: #ef4444;
      --pending: #f59e0b;
      --sidebar-w: 260px;
      --detail-w: 340px;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", Calibri, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .layout {
      display: grid;
      grid-template-columns: var(--sidebar-w) 1fr var(--detail-w);
      min-height: 100vh;
    }
    .sidebar, .detail {
      background: var(--panel);
      border-right: 1px solid var(--border);
      padding: 16px;
      overflow: auto;
    }
    .detail {
      border-right: none;
      border-left: 1px solid var(--border);
    }
    .main {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      padding: 12px 16px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
    }
    .toolbar h1 {
      margin: 0;
      font-size: 1.05rem;
      margin-right: auto;
    }
    .toolbar input, .toolbar select, .toolbar button {
      font: inherit;
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
    }
    .toolbar button {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
      cursor: pointer;
    }
    .toolbar button:hover { background: var(--accent-hover); }
    .stats-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      padding: 10px 16px;
      background: var(--panel-2);
      border-bottom: 1px solid var(--border);
      font-size: 0.85rem;
      color: var(--muted);
    }
    .stats-bar strong { color: var(--text); }
    .tabs {
      display: flex;
      gap: 4px;
      padding: 10px 16px 0;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
    }
    .tab {
      padding: 8px 14px;
      border: 1px solid transparent;
      border-bottom: none;
      border-radius: 8px 8px 0 0;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      font: inherit;
    }
    .tab.active {
      background: var(--panel-2);
      color: var(--text);
      border-color: var(--border);
    }
    .panel {
      display: none;
      flex: 1;
      padding: 12px;
      min-height: 0;
    }
    .panel.active { display: block; }
    .panel-inner {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      height: calc(100vh - 170px);
    }
    .sidebar h2, .detail h2 {
      margin: 0 0 12px;
      font-size: 0.95rem;
    }
    .sidebar label {
      display: block;
      font-size: 0.8rem;
      color: var(--muted);
      margin: 10px 0 4px;
    }
    .sidebar select, .sidebar input {
      width: 100%;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      font: inherit;
    }
    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .chip {
      font-size: 0.75rem;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .detail-empty {
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.5;
    }
    .detail-block {
      margin-bottom: 14px;
      font-size: 0.85rem;
      line-height: 1.45;
    }
    .detail-block h3 {
      margin: 0 0 6px;
      font-size: 0.8rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .status-success { color: var(--success); font-weight: 600; }
    .status-failed { color: var(--failed); font-weight: 600; }
    .status-pending, .status-missing { color: var(--pending); font-weight: 600; }
    .matrix-wrap {
      overflow: auto;
      height: calc(100vh - 170px);
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    table.matrix {
      border-collapse: collapse;
      font-size: 11px;
      min-width: 100%;
    }
    table.matrix th, table.matrix td {
      border: 1px solid var(--border);
      padding: 4px 6px;
      text-align: center;
      white-space: nowrap;
    }
    table.matrix th {
      position: sticky;
      top: 0;
      background: #151b24;
      z-index: 2;
    }
    table.matrix th.sticky-col, table.matrix td.sticky-col {
      position: sticky;
      left: 0;
      background: #151b24;
      z-index: 1;
      text-align: left;
      max-width: 180px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .cell-s { background: rgba(34,197,94,0.25); color: var(--success); }
    .cell-f { background: rgba(239,68,68,0.22); color: var(--failed); }
    .cell-p { background: rgba(245,158,11,0.18); color: var(--pending); }
    .cell-m { background: rgba(148,163,184,0.12); color: var(--muted); }
    .cell-split {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1px;
      min-width: 34px;
      padding: 0 !important;
    }
    .cell-split span {
      display: block;
      padding: 3px 2px;
      font-size: 10px;
      font-weight: 600;
    }
    .matrix th.agent-head {
      border-bottom: none;
      font-size: 10px;
    }
    .matrix th.validator-head {
      font-size: 9px;
      color: var(--muted);
      top: 22px;
    }
    .validator-tag {
      display: inline-block;
      font-size: 0.72rem;
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid var(--border);
      margin-right: 6px;
    }
    .team-row { background: rgba(99, 102, 241, 0.1) !important; }
    .team-row .tabulator-cell:first-child {
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .source-db {
      color: #86efac;
      border: 1px solid #166534;
      background: rgba(22, 101, 52, 0.25);
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.75rem;
    }
    .source-api {
      color: #fde68a;
      border: 1px solid #92400e;
      background: rgba(146, 64, 14, 0.25);
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.75rem;
    }
    .team-summary-box {
      margin-top: 12px;
      padding: 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel-2);
      font-size: 0.82rem;
      line-height: 1.5;
      color: var(--muted);
    }
    .team-summary-box strong { color: var(--text); }
    .sidebar label.checkbox {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      color: var(--text);
      cursor: pointer;
    }
    .sidebar label.checkbox input { width: auto; }
    .loading {
      padding: 24px;
      color: var(--muted);
    }
    .error {
      padding: 16px;
      color: #fecaca;
      background: rgba(127,29,29,0.25);
      border: 1px solid #7f1d1d;
      border-radius: 8px;
      margin: 12px;
    }
    .chart-panel {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 12px;
      height: calc(100vh - 170px);
      overflow: auto;
    }
    .chart-box {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
    }
    .chart-box h3 {
      margin: 0 0 4px;
      font-size: 0.92rem;
      color: var(--text);
    }
    .chart-caption {
      margin: 0 0 12px;
      font-size: 0.78rem;
      color: var(--muted);
    }
    .chart-canvas-wrap {
      position: relative;
      height: 340px;
    }
    @media (max-width: 1100px) {
      .layout { grid-template-columns: 1fr; }
      .sidebar, .detail { border: none; border-bottom: 1px solid var(--border); }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <h2>Race & Filters</h2>
      <label for="race-select">Race</label>
      <select id="race-select"></select>
      <label for="top-agents">Top agents to load</label>
      <select id="top-agents">
        <option value="20">Top 20</option>
        <option value="40" selected>Top 40</option>
        <option value="60">Top 60</option>
        <option value="100">Top 100</option>
        <option value="all">All</option>
      </select>
      <label for="agent-filter">Agent name filter</label>
      <input id="agent-filter" placeholder="e.g. pig, my_07">
      <label for="category-filter">Category</label>
      <select id="category-filter">
        <option value="">All categories</option>
        <option value="Product">Product</option>
        <option value="Shop">Shop</option>
        <option value="Voucher">Voucher</option>
      </select>
      <label for="status-filter">Problem status</label>
      <select id="status-filter">
        <option value="">Any status</option>
        <option value="SUCCESS">Solved (either validator)</option>
        <option value="BOTH">Both validators pass</option>
        <option value="SPLIT">Split (V1/V2 disagree)</option>
        <option value="FAILED">Both failed</option>
        <option value="PENDING">Pending</option>
      </select>
      <label for="matrix-mode">Matrix columns</label>
      <select id="matrix-mode">
        <option value="dual" selected>V1 + V2 (dual)</option>
        <option value="combined">Combined</option>
      </select>
      <label class="checkbox"><input type="checkbox" id="team-only"> Our group only</label>
      <div class="team-summary-box" id="team-summary-box">Group info loading…</div>
      <div class="chip-row" id="race-chips"></div>
      <div class="chip-row" id="validator-chips"></div>
    </aside>

    <section class="main">
      <div class="toolbar">
        <h1>Race Agent Dashboard</h1>
        <span id="fetch-status" style="color:var(--muted);font-size:0.85rem"></span>
        <button id="refresh-btn" type="button">Refresh</button>
      </div>
      <div class="stats-bar" id="stats-bar">Loading…</div>
      <div class="tabs">
        <button class="tab active" data-tab="agents">Agent Stats</button>
        <button class="tab" data-tab="team">Our Group</button>
        <button class="tab" data-tab="team-ranks">Group Ranks</button>
        <button class="tab" data-tab="problems">Problems</button>
        <button class="tab" data-tab="matrix">Matrix</button>
        <button class="tab" data-tab="categories">Categories</button>
        <button class="tab" data-tab="history">Race History (10)</button>
      </div>
      <div class="panel active" id="panel-agents"><div class="panel-inner" id="agents-table"></div></div>
      <div class="panel" id="panel-team"><div class="panel-inner" id="team-table"></div></div>
      <div class="panel" id="panel-team-ranks">
        <div class="chart-panel">
          <div class="chart-box">
            <h3>Rank trend by race</h3>
            <p class="chart-caption">Best rank per group member per race · lower is better · gaps = did not race or no rank yet</p>
            <div class="chart-canvas-wrap"><canvas id="team-rank-line-chart"></canvas></div>
            <div id="team-rank-line-empty" class="loading" style="display:none">No group rank data yet.</div>
          </div>
          <div class="chart-box">
            <h3>Rank distribution per race</h3>
            <p class="chart-caption">Grouped bars per race · bar height = rank (lower is better)</p>
            <div class="chart-canvas-wrap"><canvas id="team-rank-bar-chart"></canvas></div>
            <div id="team-rank-bar-empty" class="loading" style="display:none">No group rank data yet.</div>
          </div>
        </div>
      </div>
      <div class="panel" id="panel-problems"><div class="panel-inner" id="problems-table"></div></div>
      <div class="panel" id="panel-matrix"><div class="matrix-wrap" id="matrix-wrap"></div></div>
      <div class="panel" id="panel-categories"><div class="panel-inner" id="categories-table"></div></div>
      <div class="panel" id="panel-history"><div class="panel-inner" id="history-table"></div></div>
    </section>

    <aside class="detail" id="detail-panel">
      <h2>Detail</h2>
      <div class="detail-empty">Select an agent or problem row to inspect validator results, query text, and known answers.</div>
    </aside>
  </div>

  <script>
    let payload = null;
    let agentHistory = null;
    let agentsTable = null;
    let teamTable = null;
    let problemsTable = null;
    let categoriesTable = null;
    let historyTable = null;
    let historyTableRaceKey = "";
    let teamRankLineChart = null;
    let teamRankBarChart = null;
    let activeTab = "agents";

    const MEMBER_COLORS = [
      "#6366f1", "#22c55e", "#f59e0b", "#ef4444", "#06b6d4",
      "#a855f7", "#ec4899", "#84cc16", "#f97316", "#14b8a6",
    ];

    const fmtPct = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
    const fmtNum = (v) => v == null ? "—" : String(v);
    const fmtScore = (v) => v == null ? "—" : Number(v).toFixed(4);

    function statusClass(st) {
      if (st === "SUCCESS") return "status-success";
      if (st === "FAILED") return "status-failed";
      return "status-pending";
    }

    function statusShort(st) {
      if (st === "SUCCESS") return "S";
      if (st === "FAILED") return "F";
      if (st === "PENDING") return "P";
      return "·";
    }

    function cellClass(st) {
      if (st === "SUCCESS") return "cell-s";
      if (st === "FAILED") return "cell-f";
      if (st === "PENDING") return "cell-p";
      return "cell-m";
    }

    function validatorLabels() {
      return payload?.race_validators || [{ label: "V1" }, { label: "V2" }];
    }

    function validatorStatus(cell, index) {
      const validators = cell?.validators || [];
      return validators[index]?.status || "MISSING";
    }

    function problemMatchesStatus(cell, filterStatus) {
      if (!filterStatus) return true;
      if (!cell || cell.status === "MISSING") return filterStatus === "PENDING";
      const v1 = cell.v1_status || validatorStatus(cell, 0);
      const v2 = cell.v2_status || validatorStatus(cell, 1);
      if (filterStatus === "SUCCESS") return cell.status === "SUCCESS";
      if (filterStatus === "BOTH") return v1 === "SUCCESS" && v2 === "SUCCESS";
      if (filterStatus === "SPLIT") return (v1 === "SUCCESS") !== (v2 === "SUCCESS");
      if (filterStatus === "FAILED") return v1 === "FAILED" && v2 === "FAILED";
      if (filterStatus === "PENDING") return cell.status === "PENDING";
      return true;
    }

    function statusFormatter(cell) {
      const v = cell.getValue();
      const cls = v === "SUCCESS" ? "status-success" : v === "FAILED" ? "status-failed" : "status-pending";
      return `<span class="${cls}">${v || "—"}</span>`;
    }

    async function fetchJson(url) {
      const resp = await fetch(url);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || resp.statusText);
      return data;
    }

    async function loadRaceList() {
      const data = await fetchJson("/api/races");
      const select = document.getElementById("race-select");
      select.innerHTML = "";
      for (const race of data.races) {
        const opt = document.createElement("option");
        opt.value = race.race_number;
        const winner = race.winner_agent_name ? ` · ${race.winner_agent_name}` : "";
        const cached = race.cached ? " · DB" : "";
        opt.textContent = `Race ${race.race_number} (${race.status})${cached}${winner}`;
        select.appendChild(opt);
      }
      if (data.races.length) {
        select.value = data.races[0].race_number;
      }
    }

    function currentFilters() {
      return {
        race: document.getElementById("race-select").value,
        top: document.getElementById("top-agents").value,
        agentFilter: document.getElementById("agent-filter").value.trim().toLowerCase(),
        category: document.getElementById("category-filter").value,
        status: document.getElementById("status-filter").value,
      };
    }

    function filteredAgents() {
      if (!payload) return [];
      const f = currentFilters();
      const teamOnly = document.getElementById("team-only").checked;
      return payload.agents.filter((a) => {
        if (teamOnly && !a.is_team) return false;
        return !f.agentFilter || a.agent_name.toLowerCase().includes(f.agentFilter);
      });
    }

    function filteredProblems() {
      if (!payload) return [];
      const f = currentFilters();
      const agents = filteredAgents();
      return payload.problems.filter((p) => {
        if (f.category && p.category !== f.category) return false;
        if (!f.status) return true;
        if (agents.length === 0) return true;
        return agents.some((a) => problemMatchesStatus(p.by_agent[a.agent_version_id], f.status));
      });
    }

    function historyRacesForRecord(record) {
      if (!record) return [];
      if (record.races?.length) return record.races;
      const raceNumbers = agentHistory?.recent_race_numbers || [];
      const rows = [];
      for (const raceNumber of raceNumbers) {
        const rank = record[`race_${raceNumber}_rank`];
        const score = record[`race_${raceNumber}_score`];
        if (rank == null && score == null) continue;
        rows.push({
          race_number: raceNumber,
          race_rank: rank,
          race_score: score,
          is_winner: rank === 1,
        });
      }
      return rows;
    }

    function agentHistoryForName(name) {
      if (!agentHistory?.agents || !name) return null;
      const key = String(name).trim().toLowerCase();
      return agentHistory.agents.find((item) => item.agent_name.toLowerCase() === key) || null;
    }

    function agentHistoryForAgent(agent) {
      if (!agentHistory?.agents || !agent) return null;
      const versionId = agent.agent_version_id;
      if (versionId) {
        const byVersion = agentHistory.agents.find((item) => item.agent_version_id === versionId);
        if (byVersion) return byVersion;
      }
      return agentHistoryForName(agent.agent_name);
    }

    function raceHistoryRowsForAgent(agent) {
      const record = typeof agent === "string" ? agentHistoryForName(agent) : agentHistoryForAgent(agent);
      return historyRacesForRecord(record);
    }

    function renderRaceHistoryDetail(agentName, races) {
      if (!races.length) {
        return '<div class="detail-block"><h3>Race history</h3><div style="color:var(--muted)">No prior races found.</div></div>';
      }
      const rows = races.map((race) => {
        const winner = race.is_winner ? ' <span class="status-success">Winner</span>' : "";
        const bothPct = race.both_pass_rate != null ? fmtPct(race.both_pass_rate) : "—";
        const eitherPct = race.pass_rate != null ? fmtPct(race.pass_rate) : "—";
        const stats = race.problems_total != null
          ? `${race.both_success_count ?? "?"}/${race.problems_total} both · ${eitherPct} either`
          : "—";
        return `<tr>
          <td><strong>${race.race_number}</strong></td>
          <td>${race.race_status || "—"}</td>
          <td>${fmtNum(race.race_rank)}</td>
          <td>${fmtNum(race.race_score)}</td>
          <td>${bothPct}</td>
          <td>${stats}${winner}</td>
        </tr>`;
      }).join("");
      return `
        <div class="detail-block"><h3>Race history (${races.length})</h3>
          <table style="width:100%;font-size:0.78rem;border-collapse:collapse">
            <thead><tr>
              <th align="left">Race</th><th>Status</th><th>Rank</th><th>Score</th><th>Both %</th><th>Stats</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    }

    const HISTORY_RACE_LIMIT = 10;
    let renderAllTimer = null;

    function scheduleRenderAll() {
      clearTimeout(renderAllTimer);
      renderAllTimer = setTimeout(renderAll, 120);
    }

    function historyTableColumns() {
      const raceNumbers = agentHistory?.recent_race_numbers || [];
      const raceCols = raceNumbers.map((raceNumber) => ({
        title: `R${raceNumber}`,
        field: `race_${raceNumber}_score`,
        width: 72,
        hozAlign: "center",
        headerTooltip: "Sort by score (higher is better) · rank / score",
        headerSortStartingDir: "desc",
        sorter: (a, b) => {
          if (a == null && b == null) return 0;
          if (a == null) return 1;
          if (b == null) return -1;
          return a - b;
        },
        formatter: (cell) => {
          const row = cell.getRow().getData();
          const rank = row[`race_${raceNumber}_rank`];
          if (rank == null && row[`race_${raceNumber}_score`] == null) return "—";
          return `<div>${fmtNum(rank)}</div><div style="color:var(--muted);font-size:10px">${fmtScore(row[`race_${raceNumber}_score`])}</div>`;
        },
      }));
      return [
        { title: "Agent", field: "agent_name", minWidth: 120 },
        { title: "Version", field: "agent_version_id", width: 100, formatter: (c) => {
          const v = c.getValue() || "";
          return v ? `${v.slice(0, 8)}…` : "—";
        }, tooltip: true },
        { title: "Member", field: "team_name", width: 90 },
        { title: "Races", field: "race_count", width: 70, sorter: "number" },
        { title: "Wins", field: "wins", width: 65, sorter: "number" },
        { title: "Best rank", field: "best_rank", width: 90, sorter: "number" },
        { title: "Avg rank", field: "avg_rank", width: 85, sorter: "number" },
        { title: "Avg score", field: "avg_score", width: 90, sorter: "number", formatter: (c) => fmtNum(c.getValue()) },
        { title: "Avg both %", field: "avg_both_pass_rate", width: 95, sorter: "number", formatter: (c) => fmtPct(c.getValue()) },
        { title: "Latest", field: "latest_race", width: 75, sorter: "number" },
        ...raceCols,
      ];
    }

    async function loadAgentHistory(force=false) {
      try {
        const qs = new URLSearchParams({ limit: String(HISTORY_RACE_LIMIT) });
        if (force) qs.set("force", "1");
        agentHistory = await fetchJson(`/api/agent-history?${qs}`);
      } catch (_err) {
        agentHistory = null;
      }
    }

    function filteredHistoryAgents() {
      if (!agentHistory?.agents) return [];
      const filter = document.getElementById("agent-filter").value.trim().toLowerCase();
      const teamOnly = document.getElementById("team-only").checked;
      const rows = [];
      for (const item of agentHistory.agents) {
        if (!(item.race_count > 0)) continue;
        if (filter && !item.agent_name.toLowerCase().includes(filter)) continue;
        if (teamOnly && !item.is_team) continue;
        rows.push(item);
      }
      return rows;
    }

    function renderHistoryTable(forceRebuild=false) {
      const data = filteredHistoryAgents();
      const raceKey = (agentHistory?.recent_race_numbers || []).join(",");
      const needsRebuild = forceRebuild || !historyTable || raceKey !== historyTableRaceKey;
      if (needsRebuild) {
        if (historyTable) historyTable.destroy();
        historyTableRaceKey = raceKey;
        historyTable = new Tabulator("#history-table", {
          data,
          columns: historyTableColumns(),
          layout: "fitColumns",
          height: "100%",
          index: "history_id",
          pagination: true,
          paginationSize: 100,
          paginationSizeSelector: [50, 100, 200, 500],
          selectable: 1,
          placeholder: agentHistory ? "No agents match filters" : "Loading race history…",
          initialSort: [{ column: "best_rank", dir: "asc" }],
          rowFormatter: (row) => {
            row.getElement().classList.toggle("team-row", !!row.getData().is_team);
          },
        });
        historyTable.on("rowClick", (_, row) => showAgentHistoryDetail(row.getData()));
        return;
      }
      historyTable.setData(data);
    }

    function destroyTeamRankCharts() {
      if (teamRankLineChart) {
        teamRankLineChart.destroy();
        teamRankLineChart = null;
      }
      if (teamRankBarChart) {
        teamRankBarChart.destroy();
        teamRankBarChart = null;
      }
    }

    function chartThemeOptions() {
      const text = "#93a0b5";
      const grid = "#2a3444";
      return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            labels: { color: text, boxWidth: 12, font: { size: 11 } },
          },
        },
        scales: {
          x: {
            ticks: { color: text },
            grid: { color: grid },
            title: { display: true, text: "Race #", color: text },
          },
          y: {
            reverse: true,
            beginAtZero: false,
            ticks: { color: text, stepSize: 1 },
            grid: { color: grid },
            title: { display: true, text: "Rank (lower is better)", color: text },
          },
        },
      };
    }

    function renderTeamRankCharts() {
      destroyTeamRankCharts();
      const chartData = agentHistory?.team_ranks;
      const lineCanvas = document.getElementById("team-rank-line-chart");
      const barCanvas = document.getElementById("team-rank-bar-chart");
      if (!lineCanvas || !barCanvas || typeof Chart === "undefined") return;

      if (!chartData?.members?.length) {
        document.getElementById("team-rank-line-empty").style.display = "block";
        document.getElementById("team-rank-bar-empty").style.display = "block";
        lineCanvas.parentElement.style.display = "none";
        barCanvas.parentElement.style.display = "none";
        return;
      }
      document.getElementById("team-rank-line-empty").style.display = "none";
      document.getElementById("team-rank-bar-empty").style.display = "none";
      lineCanvas.parentElement.style.display = "block";
      barCanvas.parentElement.style.display = "block";

      const labels = (chartData.race_numbers || []).map((n) => String(n));
      const datasets = chartData.members.map((member, index) => ({
        label: member.label,
        data: member.ranks,
        agentNames: member.agents_by_race || [],
        borderColor: MEMBER_COLORS[index % MEMBER_COLORS.length],
        backgroundColor: MEMBER_COLORS[index % MEMBER_COLORS.length],
        tension: 0.15,
        spanGaps: false,
        pointRadius: 4,
        pointHoverRadius: 6,
      }));

      const tooltipLabel = (ctx) => {
        const rank = ctx.parsed.y;
        const label = ctx.dataset.label || "";
        const agentName = ctx.dataset.agentNames?.[ctx.dataIndex];
        const agentSuffix = agentName ? ` (${agentName})` : "";
        return rank == null ? `${label}: —` : `${label}: rank ${rank}${agentSuffix}`;
      };

      teamRankLineChart = new Chart(lineCanvas, {
        type: "line",
        data: { labels, datasets },
        options: {
          ...chartThemeOptions(),
          plugins: {
            ...chartThemeOptions().plugins,
            tooltip: { callbacks: { label: tooltipLabel } },
          },
        },
      });

      teamRankBarChart = new Chart(barCanvas, {
        type: "bar",
        data: { labels, datasets: datasets.map((item) => ({ ...item, borderWidth: 1 })) },
        options: {
          ...chartThemeOptions(),
          plugins: {
            ...chartThemeOptions().plugins,
            tooltip: { callbacks: { label: tooltipLabel } },
          },
          scales: {
            ...chartThemeOptions().scales,
            x: { ...chartThemeOptions().scales.x, stacked: false },
            y: { ...chartThemeOptions().scales.y, stacked: false },
          },
        },
      });
    }

    function showAgentHistoryDetail(record) {
      const races = historyRacesForRecord(record);
      document.getElementById("detail-panel").innerHTML = `
        <h2>Agent History</h2>
        <div class="detail-block"><h3>Name</h3>${escapeHtml(record.agent_name)}</div>
        <div class="detail-block"><h3>Summary (last ${agentHistory?.recent_race_limit ?? HISTORY_RACE_LIMIT} races)</h3>
          <div>Races: <strong>${record.race_count ?? 0}</strong> · Wins: <strong>${record.wins ?? 0}</strong></div>
          <div>Best rank: <strong>${fmtNum(record.best_rank)}</strong> · Avg rank: <strong>${fmtNum(record.avg_rank)}</strong></div>
          <div>Avg score: <strong>${fmtNum(record.avg_score)}</strong> · Avg both pass: <strong>${fmtPct(record.avg_both_pass_rate)}</strong></div>
        </div>
        ${renderRaceHistoryDetail(record.agent_name, races)}
        <div class="detail-block"><h3>Version id</h3><code style="font-size:0.75rem;word-break:break-all">${escapeHtml(record.agent_version_id || "—")}</code></div>
        <div class="detail-block"><h3>Hotkey</h3><code style="font-size:0.75rem;word-break:break-all">${escapeHtml(record.miner_hotkey || "—")}</code></div>
      `;
    }

    function agentStatsForCategory(agent, category) {
      if (!category || !payload?.problems) return agent;
      let total = 0;
      let v1 = 0;
      let v2 = 0;
      let both = 0;
      let split = 0;
      let either = 0;
      let bothFail = 0;
      let pending = 0;
      let failed = 0;
      const execSamples = [];
      for (const prob of payload.problems) {
        if (prob.category !== category) continue;
        total += 1;
        const cell = prob.by_agent?.[agent.agent_version_id];
        if (!cell || cell.status === "MISSING") {
          pending += 1;
          continue;
        }
        const v1s = validatorStatus(cell, 0);
        const v2s = validatorStatus(cell, 1);
        if (v1s === "SUCCESS") v1 += 1;
        if (v2s === "SUCCESS") v2 += 1;
        if (v1s === "SUCCESS" && v2s === "SUCCESS") both += 1;
        else if (v1s === "SUCCESS" || v2s === "SUCCESS") split += 1;
        else if (v1s === "FAILED" && v2s === "FAILED") bothFail += 1;
        if (cell.status === "SUCCESS") either += 1;
        else if (cell.status === "FAILED") failed += 1;
        else pending += 1;
        if (cell.avg_exec_s != null) execSamples.push(cell.avg_exec_s);
      }
      return {
        ...agent,
        stats_scope: category,
        problems_total: total,
        success_count: either,
        failed_count: failed,
        pending_count: pending,
        v1_success_count: v1,
        v2_success_count: v2,
        both_success_count: both,
        split_success_count: split,
        both_failed_count: bothFail,
        pass_rate: total ? either / total : 0,
        v1_pass_rate: total ? v1 / total : 0,
        v2_pass_rate: total ? v2 / total : 0,
        both_pass_rate: total ? both / total : 0,
        avg_exec_s: execSamples.length
          ? Math.round((execSamples.reduce((sum, value) => sum + value, 0) / execSamples.length) * 10) / 10
          : null,
      };
    }

    function agentsForStatsTable(sourceAgents) {
      const category = currentFilters().category;
      return sourceAgents.map((agent) => agentStatsForCategory(agent, category));
    }

    function renderStatsBar() {
      const bar = document.getElementById("stats-bar");
      if (!payload) { bar.textContent = "Loading…"; return; }
      const s = payload.summary;
      const r = payload.race;
      bar.innerHTML = `
        <span><strong>Race ${r.race_number}</strong> ${r.status}</span>
        <span class="${payload.source === "database" ? "source-db" : "source-api"}">${payload.source === "database" ? "Cached DB" : "Live API"}</span>
        <span>Agents loaded: <strong>${s.agent_count_loaded}/${s.agent_count_total}</strong></span>
        <span>Problems: <strong>${s.problem_count}</strong></span>
        ${s.problem_count === 0 ? '<span style="color:var(--pending)">Race not evaluated yet — try a completed race</span>' : ''}
        <span>Known answers: <strong>${s.known_answers}</strong></span>
        <span>Group in race: <strong>${payload.team?.members_in_race ?? 0}</strong></span>
        <span>Updated: <strong>${new Date(payload.fetched_at).toLocaleString()}</strong></span>
      `;
      const chips = document.getElementById("race-chips");
      chips.innerHTML = `
        <span class="chip">Winner: ${r.winner_agent_name || "TBD"}</span>
        <span class="chip">Score: ${r.winner_score ?? "—"}</span>
        <span class="chip">Qualifiers: ${r.qualifier_count ?? "—"}</span>
      `;
      const vChips = document.getElementById("validator-chips");
      const vals = validatorLabels();
      vChips.innerHTML = vals.map((v) =>
        `<span class="chip validator-tag">${v.label}: …${escapeHtml(v.validator_short || "?")}</span>`
      ).join("");
      renderTeamSummaryBox();
    }

    function renderTeamSummaryBox() {
      const box = document.getElementById("team-summary-box");
      if (!payload?.team) {
        box.textContent = "Group info unavailable.";
        return;
      }
      const t = payload.team;
      const rs = t.registration_summary || {};
      box.innerHTML = `
        <div><strong>SN15 group</strong> · ${rs.team_registered_members ?? "?"}/${rs.team_members ?? Object.keys(t.team_coldkeys || {}).length} members registered</div>
        <div>UIDs: <strong>${rs.team_uids ?? t.team_uids_on_subnet ?? "?"}</strong> · Daily τ: <strong>${rs.team_daily_tao ?? t.team_daily_tao ?? "?"}</strong> (${rs.team_daily_tao_pct ?? t.team_daily_tao_pct ?? "?"}%)</div>
        <div>In this race: <strong>${t.members_in_race ?? 0}</strong> agents · Best rank <strong>${t.best_rank ?? "—"}</strong> (${escapeHtml(t.best_agent_name || "—")})</div>
      `;
    }

    function agentStatColumns() {
      const vals = validatorLabels();
      const v1Label = vals[0]?.label || "V1";
      const v2Label = vals[1]?.label || "V2";
      const category = currentFilters().category;
      const scope = category ? ` (${category})` : "";
      return [
        { title: "Rank", field: "race_rank", width: 70, sorter: "number" },
        { title: "Member", field: "team_name", width: 90 },
        { title: "Agent", field: "agent_name", minWidth: 120 },
        { title: "UID", field: "uid", width: 55, sorter: "number" },
        { title: "Score", field: "race_score", width: 80, sorter: "number", formatter: (c) => fmtNum(c.getValue()) },
        { title: `${v1Label}${scope}`, field: "v1_success_count", width: 72, sorter: "number" },
        { title: `${v2Label}${scope}`, field: "v2_success_count", width: 72, sorter: "number" },
        { title: `Both${scope}`, field: "both_success_count", width: 72, sorter: "number" },
        { title: `Split${scope}`, field: "split_success_count", width: 72, sorter: "number" },
        { title: `Either${scope}`, field: "success_count", width: 72, sorter: "number" },
        { title: `Both %${scope}`, field: "both_pass_rate", width: 82, sorter: "number", formatter: (c) => fmtPct(c.getValue()) },
        { title: `N${scope}`, field: "problems_total", width: 58, sorter: "number", headerTooltip: category ? `${category} problems in race` : "Problems in race" },
        { title: "Avg s", field: "avg_exec_s", width: 65, sorter: "number" },
      ];
    }

    function renderAgentsTable() {
      const data = agentsForStatsTable(filteredAgents());
      if (agentsTable) agentsTable.destroy();
      agentsTable = new Tabulator("#agents-table", {
        data,
        columns: agentStatColumns(),
        layout: "fitColumns",
        height: "100%",
        pagination: true,
        paginationSize: 50,
        selectable: 1,
        initialSort: [{ column: "race_rank", dir: "asc" }],
        rowFormatter: (row) => {
          row.getElement().classList.toggle("team-row", !!row.getData().is_team);
        },
      });
      agentsTable.on("rowClick", (_, row) => showAgentDetail(row.getData()));
    }

    function renderTeamTable() {
      const data = agentsForStatsTable((payload?.team?.agents || []).slice());
      if (teamTable) teamTable.destroy();
      teamTable = new Tabulator("#team-table", {
        data,
        columns: [
          ...agentStatColumns(),
          { title: "Coldkey", field: "miner_coldkey", minWidth: 120, formatter: (c) => {
            const v = c.getValue() || "";
            return v ? `${v.slice(0, 8)}…${v.slice(-4)}` : "—";
          }},
        ],
        layout: "fitColumns",
        height: "100%",
        selectable: 1,
        placeholder: "No group agents in this race",
        initialSort: [{ column: "race_rank", dir: "asc" }],
        rowFormatter: (row) => row.getElement().classList.add("team-row"),
      });
      teamTable.on("rowClick", (_, row) => showAgentDetail(row.getData()));
    }

    function renderProblemsTable() {
      const rows = filteredProblems();
      const vals = validatorLabels();
      const columns = [
        { title: "Code", field: "query_code", width: 70 },
        { title: "Category", field: "category", width: 90 },
        { title: "Both OK", field: "both_ok_count", width: 72, sorter: "number" },
        { title: "Split", field: "split_count", width: 60, sorter: "number" },
        { title: "Either OK", field: "solver_count", width: 80, sorter: "number" },
        { title: "Known agent", field: "answer_agent", width: 110 },
        { title: "Query", field: "query", minWidth: 220, formatter: "textarea" },
        { title: "Answer", field: "correct_answer", width: 140 },
      ];
      const agents = filteredAgents();
      const enriched = rows.map((p) => {
        let bothOk = 0;
        let split = 0;
        for (const a of agents) {
          const cell = p.by_agent[a.agent_version_id];
          if (!cell) continue;
          const v1 = validatorStatus(cell, 0);
          const v2 = validatorStatus(cell, 1);
          if (v1 === "SUCCESS" && v2 === "SUCCESS") bothOk += 1;
          else if ((v1 === "SUCCESS") !== (v2 === "SUCCESS")) split += 1;
        }
        return { ...p, both_ok_count: bothOk, split_count: split };
      });
      if (problemsTable) problemsTable.destroy();
      problemsTable = new Tabulator("#problems-table", {
        data: enriched,
        columns,
        layout: "fitColumns",
        height: "100%",
        selectable: 1,
        initialSort: [{ column: "solver_count", dir: "desc" }],
      });
      problemsTable.on("rowClick", (_, row) => showProblemDetail(row.getData()));
    }

    function renderCategoriesTable() {
      if (!payload) return;
      const rows = Object.entries(payload.category_stats).map(([category, stats]) => {
        const total = stats.success + stats.failed + stats.pending;
        return {
          category,
          success: stats.success,
          failed: stats.failed,
          pending: stats.pending,
          total,
          pass_rate: total ? stats.success / total : 0,
          agents: stats.agents,
        };
      });
      const columns = [
        { title: "Category", field: "category", width: 120 },
        { title: "Success", field: "success", width: 90, sorter: "number" },
        { title: "Failed", field: "failed", width: 90, sorter: "number" },
        { title: "Pending", field: "pending", width: 90, sorter: "number" },
        { title: "Pass rate", field: "pass_rate", width: 100, formatter: (c) => fmtPct(c.getValue()) },
        { title: "Agent slots", field: "agents", width: 110, sorter: "number" },
      ];
      if (categoriesTable) categoriesTable.destroy();
      categoriesTable = new Tabulator("#categories-table", {
        data: rows,
        columns,
        layout: "fitColumns",
        height: "100%",
      });
    }

    function renderMatrix() {
      const wrap = document.getElementById("matrix-wrap");
      if (!payload) { wrap.innerHTML = '<div class="loading">Loading…</div>'; return; }
      const agents = filteredAgents().slice(0, 20);
      const problems = filteredProblems().slice(0, 80);
      const dualMode = document.getElementById("matrix-mode").value === "dual";
      const vals = validatorLabels();
      if (!agents.length || !problems.length) {
        wrap.innerHTML = '<div class="loading">No data for matrix.</div>';
        return;
      }
      let html = '<table class="matrix"><thead>';
      if (dualMode) {
        html += '<tr><th class="sticky-col" rowspan="2">Problem</th>';
        for (const a of agents) {
          html += `<th class="agent-head" colspan="2" title="${escapeHtml(a.agent_name)}">${escapeHtml(a.agent_name.slice(0, 14))}</th>`;
        }
        html += '</tr><tr>';
        for (const _a of agents) {
          html += `<th class="validator-head">${vals[0]?.label || "V1"}</th><th class="validator-head">${vals[1]?.label || "V2"}</th>`;
        }
        html += '</tr></thead><tbody>';
        for (const p of problems) {
          const label = p.query_code || p.problem_id.slice(0, 8);
          html += `<tr><td class="sticky-col" title="${escapeHtml(p.query || p.problem_id)}">${escapeHtml(label)}</td>`;
          for (const a of agents) {
            const cell = p.by_agent[a.agent_version_id] || { status: "MISSING", validators: [] };
            for (const idx of [0, 1]) {
              const st = validatorStatus(cell, idx);
              const vr = cell.validators?.[idx];
              const title = `${a.agent_name} ${vals[idx]?.label || ("V" + (idx + 1))}: ${st}` +
                (vr?.score != null ? ` score=${vr.score}` : "") +
                (vr?.reasoning_score != null ? ` reasoning=${vr.reasoning_score}` : "");
              html += `<td class="${cellClass(st)}" title="${escapeHtml(title)}">${statusShort(st)}</td>`;
            }
          }
          html += '</tr>';
        }
      } else {
        html += '<tr><th class="sticky-col">Problem</th>';
        for (const a of agents) {
          html += `<th title="${escapeHtml(a.agent_name)}">${escapeHtml(a.agent_name.slice(0, 12))}</th>`;
        }
        html += '</tr></thead><tbody>';
        for (const p of problems) {
          const label = p.query_code || p.problem_id.slice(0, 8);
          html += `<tr><td class="sticky-col" title="${escapeHtml(p.query || p.problem_id)}">${escapeHtml(label)}</td>`;
          for (const a of agents) {
            const cell = p.by_agent[a.agent_version_id] || { status: "MISSING", validators: [] };
            const v1 = validatorStatus(cell, 0);
            const v2 = validatorStatus(cell, 1);
            const title = `${a.agent_name}: ${v1}/${v2} (combined ${cell.status || "MISSING"})`;
            html += `<td class="cell-split" title="${escapeHtml(title)}"><span class="${cellClass(v1)}">${statusShort(v1)}</span><span class="${cellClass(v2)}">${statusShort(v2)}</span></td>`;
          }
          html += '</tr>';
        }
      }
      html += '</tbody></table>';
      wrap.innerHTML = html;
    }

    function escapeHtml(text) {
      return String(text || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }

    function showAgentDetail(agent) {
      const vals = validatorLabels();
      const cats = Object.entries(agent.by_category || {}).map(([cat, b]) =>
        `<div>${cat}: ${b.success} ok / ${b.failed} fail / ${b.pending} pend (${b.total} total)</div>`
      ).join("");
      document.getElementById("detail-panel").innerHTML = `
        <h2>Agent Detail</h2>
        <div class="detail-block"><h3>Name</h3>${escapeHtml(agent.agent_name)}</div>
        <div class="detail-block"><h3>Score / Rank</h3>${fmtNum(agent.race_score)} / ${fmtNum(agent.race_rank)}</div>
        <div class="detail-block"><h3>Validator pass counts${agent.stats_scope ? ` · ${escapeHtml(agent.stats_scope)} only` : ""}</h3>
          <div><span class="validator-tag">${vals[0]?.label || "V1"}</span> <strong>${agent.v1_success_count ?? 0}</strong> / ${agent.problems_total} (${fmtPct(agent.v1_pass_rate)})</div>
          <div><span class="validator-tag">${vals[1]?.label || "V2"}</span> <strong>${agent.v2_success_count ?? 0}</strong> / ${agent.problems_total} (${fmtPct(agent.v2_pass_rate)})</div>
          <div>Both pass: <strong>${agent.both_success_count ?? 0}</strong> (${fmtPct(agent.both_pass_rate)})</div>
          <div>Split (one pass): <strong>${agent.split_success_count ?? 0}</strong></div>
          <div>Either pass: <strong>${agent.success_count ?? 0}</strong> (${fmtPct(agent.pass_rate)})</div>
          <div>Both fail: <strong>${agent.both_failed_count ?? 0}</strong></div>
        </div>
        <div class="detail-block"><h3>By category</h3>${cats || "—"}</div>
        <div class="detail-block"><h3>Group</h3>
          ${agent.is_team
            ? `<div><strong>${escapeHtml(agent.team_name || "member")}</strong> · UID ${fmtNum(agent.uid)}</div>
               <div><code style="font-size:0.75rem;word-break:break-all">${escapeHtml(agent.miner_coldkey || "—")}</code></div>`
            : "Not a group agent"}
        </div>
        <div class="detail-block"><h3>Hotkey</h3><code style="font-size:0.75rem;word-break:break-all">${escapeHtml(agent.miner_hotkey || "—")}</code></div>
        <div class="detail-block"><h3>Version id</h3><code style="font-size:0.75rem;word-break:break-all">${escapeHtml(agent.agent_version_id || "—")}</code></div>
        ${renderRaceHistoryDetail(agent.agent_name, raceHistoryRowsForAgent(agent))}
      `;
    }

    function showProblemDetail(problem) {
      const agents = filteredAgents();
      const vals = validatorLabels();
      const rows = agents.map((a) => {
        const cell = problem.by_agent[a.agent_version_id] || { status: "MISSING", validators: [] };
        const v1 = validatorStatus(cell, 0);
        const v2 = validatorStatus(cell, 1);
        return `<tr>
          <td>${escapeHtml(a.agent_name)}</td>
          <td class="${statusClass(v1)}">${v1}</td>
          <td class="${statusClass(v2)}">${v2}</td>
          <td class="${statusClass(cell.status)}">${cell.status || "MISSING"}</td>
          <td>${fmtNum(cell.avg_exec_s)}</td>
        </tr>`;
      }).join("");
      document.getElementById("detail-panel").innerHTML = `
        <h2>Problem Detail</h2>
        <div class="detail-block"><h3>Category / Code</h3>${escapeHtml(problem.category)} · ${escapeHtml(problem.query_code || "—")}</div>
        <div class="detail-block"><h3>Query</h3>${escapeHtml(problem.query || problem.problem_id)}</div>
        <div class="detail-block"><h3>Known answer</h3>${escapeHtml(problem.correct_answer || "—")} ${problem.answer_agent ? `(by ${escapeHtml(problem.answer_agent)})` : ""}</div>
        <div class="detail-block"><h3>Validators</h3>
          ${vals.map((v) => `<span class="validator-tag">${v.label} …${escapeHtml(v.validator_short || "?")}</span>`).join("")}
        </div>
        <div class="detail-block"><h3>Per-agent validator status</h3>
          <table style="width:100%;font-size:0.78rem;border-collapse:collapse">
            <thead><tr>
              <th align="left">Agent</th>
              <th>${vals[0]?.label || "V1"}</th>
              <th>${vals[1]?.label || "V2"}</th>
              <th>Either</th><th>Avg s</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    function renderAll() {
      renderStatsBar();
      renderAgentsTable();
      renderTeamTable();
      renderProblemsTable();
      renderCategoriesTable();
      if (activeTab === "matrix") renderMatrix();
      if (activeTab === "history") renderHistoryTable();
      if (activeTab === "team-ranks") renderTeamRankCharts();
    }

    async function loadRaceData(force=false) {
      const f = currentFilters();
      const statusEl = document.getElementById("fetch-status");
      statusEl.textContent = "Fetching…";
      try {
        payload = await fetchJson(`/api/race/${f.race}?top=${f.top}${force ? "&force=1" : ""}`);
        statusEl.textContent = "";
        renderAll();
      } catch (err) {
        statusEl.textContent = "";
        document.querySelector(".main").insertAdjacentHTML("beforeend", `<div class="error">${escapeHtml(err.message)}</div>`);
      }
    }

    document.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        activeTab = btn.dataset.tab;
        document.getElementById(`panel-${activeTab}`).classList.add("active");
        if (activeTab === "matrix") renderMatrix();
        if (activeTab === "team") renderTeamTable();
        if (activeTab === "history") renderHistoryTable();
        if (activeTab === "team-ranks") renderTeamRankCharts();
      });
    });

    ["race-select", "top-agents"].forEach((id) => {
      document.getElementById(id).addEventListener("change", () => loadRaceData(false));
    });
    ["agent-filter", "category-filter", "status-filter", "matrix-mode", "team-only"].forEach((id) => {
      document.getElementById(id).addEventListener("input", scheduleRenderAll);
      document.getElementById(id).addEventListener("change", scheduleRenderAll);
    });
    document.getElementById("refresh-btn").addEventListener("click", async () => {
      await loadRaceData(true);
      await loadAgentHistory(true);
      renderHistoryTable(true);
      if (activeTab === "team-ranks") renderTeamRankCharts();
    });

    (async function init() {
      await loadRaceList();
      await Promise.all([loadRaceData(false), loadAgentHistory(false)]);
      renderHistoryTable(true);
      if (activeTab === "team-ranks") renderTeamRankCharts();
    })();
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/races")
def api_races():
    try:
        races = fetch_race_list(force=request.args.get("force") == "1")
        cached = {item["race_number"] for item in list_cached_races(RACE_AGENT_DB)}
        for race in races:
            race["cached"] = int(race.get("race_number") or 0) in cached
        return jsonify({"races": races, "cached_races": sorted(cached, reverse=True)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/group")
def api_group():
    force = request.args.get("force", "").lower() in {"1", "true", "yes"}
    try:
        return jsonify(get_registration_payload(force=force))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/cache")
def api_cache():
    try:
        return jsonify({"races": list_cached_races(RACE_AGENT_DB)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/agent-history")
def api_agent_history():
    force = request.args.get("force", "").lower() in {"1", "true", "yes"}
    cache_seconds = float(request.args.get("cache", 300))
    max_races = min(max(int(request.args.get("limit", HISTORY_RACE_LIMIT)), 1), 200)
    try:
        history = build_agent_participation_history(
            force=force,
            cache_seconds=cache_seconds,
            max_races=max_races,
        )
        reg_payload = get_registration_payload(force=force)
        hotkey_map = {
            row["hotkey"]: row
            for row in (reg_payload or {}).get("rows") or []
            if row.get("hotkey")
        }
        for agent in history.get("agents") or []:
            row = hotkey_map.get(agent.get("miner_hotkey") or "")
            agent["is_team"] = bool(row and row.get("is_team"))
            agent["team_name"] = row.get("team_name") if row else None
            agent["uid"] = row.get("uid") if row else None
        history["team_ranks"] = build_team_rank_distribution(
            history.get("agents") or [],
            [{"race_number": n} for n in history.get("recent_race_numbers") or []],
        )
        api_agents = [{key: val for key, val in agent.items() if key != "races"} for agent in history.get("agents") or []]
        return jsonify(
            {
                "fetched_at": history.get("fetched_at"),
                "race_count": history.get("race_count"),
                "recent_race_limit": history.get("recent_race_limit"),
                "recent_race_numbers": history.get("recent_race_numbers"),
                "participation_count": history.get("participation_count"),
                "agents": api_agents,
                "team_ranks": history.get("team_ranks"),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/race/<int:race_number>")
def api_race(race_number: int):
    top = parse_top_agents(request.args.get("top", "40"))
    force = request.args.get("force", "").lower() in {"1", "true", "yes"}
    cache_seconds = float(request.args.get("cache", 120))
    try:
        return jsonify(get_cached_payload(race_number, top_agents=top, force=force, cache_seconds=cache_seconds))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def main() -> None:
    import argparse

    load_env()
    load_csv_index()
    load_query_map()

    parser = argparse.ArgumentParser(description="Race agent problem-solving dashboard.")
    parser.add_argument("--port", type=int, default=5056, help="Port (default: 5056)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser")
    parser.add_argument(
        "--warm-cache",
        action="store_true",
        help="Fetch and store all completed races in SQLite (slow once)",
    )
    parser.add_argument(
        "--warm-cache-only",
        action="store_true",
        help="Warm completed-race cache and exit (no dashboard)",
    )
    parser.add_argument(
        "--warm-limit",
        type=int,
        default=None,
        help="Limit number of completed races to warm (default: all)",
    )
    args = parser.parse_args()

    if args.warm_cache or args.warm_cache_only:
        cached = warm_completed_race_cache(limit=args.warm_limit)
        print(f"Cached {len(cached)} race(s): {cached or '(none new)'}")
        print(f"Database: {RACE_AGENT_DB}")
        if args.warm_cache_only:
            return

    url = f"http://127.0.0.1:{args.port}"
    print(f"Race Agent Dashboard on {url}")
    print(f"Race cache DB: {RACE_AGENT_DB}")
    cached = list_cached_races(RACE_AGENT_DB)
    if cached:
        print(f"Cached races in DB: {len(cached)} (latest: race {cached[0]['race_number']})")
    if args.host == "0.0.0.0":
        print(f"Remote access: http://<host-ip>:{args.port}")
    print("Tip: run --warm-cache-only once to preload finished races")

    if not args.no_browser:
        Timer(0.8, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
