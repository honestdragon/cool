#!/usr/bin/env python3
"""Extract failed recommend_product IDs from cached race agent payloads."""

from __future__ import annotations

import gzip
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ORO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORO_ROOT / "scripts"))

from fill_race_query_answers import extract_final_product_ids  # noqa: E402

from race_agent_db import (
    DEFAULT_DB as RACE_AGENT_DB,
    get_cached_race_payload,
    is_cacheable_status,
    save_race_payload,
)
from race_reward_analysis_db import add_failed_attempt

BASE_URL = "https://api.oroagents.com"
DEFAULT_QUERY_MAP = ORO_ROOT / "data" / "races" / "race_problem_query_map.json"
DEFAULT_CSV = Path(__file__).resolve().parent / "race-problems-queries-2026-06-22.csv"

_rate_lock = threading.Lock()
_last_request_at = 0.0


def _throttle(min_interval: float) -> None:
    global _last_request_at
    with _rate_lock:
        now = time.monotonic()
        wait = min_interval - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _request_with_retries(
    request_func,
    *,
    min_interval: float,
    retries: int = 8,
) -> object:
    delay = 2.0
    for attempt in range(retries):
        _throttle(min_interval)
        try:
            return request_func()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
        except urllib.error.URLError:
            if attempt + 1 < retries:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise


def _get_json(url: str, *, min_interval: float = 0.15, timeout: int = 60) -> dict | list:
    def _do():
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    return _request_with_retries(_do, min_interval=min_interval)


def _post_json(url: str, body: dict, *, min_interval: float = 0.15, timeout: int = 60) -> dict:
    def _do():
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    return _request_with_retries(_do, min_interval=min_interval)


def _fetch_logs(
    agent_version_id: str,
    eval_run_id: str,
    problem_id: str,
    *,
    min_interval: float,
) -> list:
    meta = _post_json(
        f"{BASE_URL}/v1/public/artifacts/download-url",
        {
            "artifact_type": "EVAL_PROBLEM_LOGS",
            "agent_version_id": agent_version_id,
            "eval_run_id": eval_run_id,
            "problem_id": problem_id,
        },
        min_interval=min_interval,
    )

    def _download():
        with urllib.request.urlopen(meta["download_url"], timeout=120) as resp:
            return resp.read()

    raw = _request_with_retries(_download, min_interval=min_interval)
    try:
        return json.loads(raw.decode())
    except Exception:
        return json.loads(gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode())


def _load_csv_index(csv_path: Path) -> dict[tuple[int, str], dict]:
    import csv

    index: dict[tuple[int, str], dict] = {}
    for row in csv.DictReader(csv_path.open(encoding="utf-8")):
        answer = (row.get("correct_answer") or "").strip()
        if not answer:
            continue
        key = (int(row["race_number"]), row["query"].strip())
        index[key] = row
    return index


def _load_query_map(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_agent_problems(race_id: str, agent_version_id: str, *, min_interval: float) -> dict[str, dict]:
    data = _get_json(
        f"{BASE_URL}/v1/public/agent-versions/{agent_version_id}/problems"
        f"?phase=RACE&race_id={race_id}",
        min_interval=min_interval,
    )
    return {p["problem_id"]: p for p in (data.get("problems") or [])}


def _collect_tasks(
    payload: dict,
    race_number: int,
    csv_index: dict[tuple[int, str], dict],
    query_map: dict[str, dict[str, str]],
) -> list[dict]:
    race_map = query_map.get(str(race_number), {})
    tasks: list[dict] = []
    for prob in payload.get("problems") or []:
        problem_id = prob["problem_id"]
        query = (race_map.get(problem_id) or prob.get("query") or "").strip()
        if not query:
            continue
        csv_row = csv_index.get((race_number, query), {})
        correct = (prob.get("correct_answer") or csv_row.get("correct_answer") or "").strip()
        if not correct:
            continue
        by_agent = prob.get("by_agent") or {}
        for agent_version_id, cell in by_agent.items():
            if cell.get("status") != "FAILED":
                continue
            tasks.append(
                {
                    "race_number": race_number,
                    "race_id": payload["race"]["race_id"],
                    "query": query,
                    "query_code": (csv_row.get("query_code") or prob.get("query_code") or ""),
                    "category": prob.get("category") or csv_row.get("category") or "",
                    "correct_answer": correct,
                    "agent_version_id": agent_version_id,
                    "agent_name": cell.get("agent_name") or "?",
                    "problem_id": problem_id,
                }
            )
    return tasks


def _norm_ids(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(sorted(part.strip() for part in value.split(",") if part.strip()))


def _ids_key(value: str | None) -> str:
    return ",".join(_norm_ids(value))


def fetch_complete_race_numbers() -> list[int]:
    races: list[int] = []
    offset = 0
    total = None
    while total is None or offset < total:
        data = _get_json(f"{BASE_URL}/v1/public/races/history?limit=100&offset={offset}")
        total = int(data.get("total") or 0)
        batch = data.get("races") or []
        if not batch:
            break
        for race in batch:
            if race.get("status") == "RACE_COMPLETE" and race.get("race_number") is not None:
                races.append(int(race["race_number"]))
        offset += len(batch)
        if len(batch) < 100:
            break
    return sorted(set(races))


def load_race_payload(
    race_number: int,
    race_cache_db: Path = RACE_AGENT_DB,
    *,
    force_refresh: bool = False,
) -> dict | None:
    if not force_refresh:
        payload = get_cached_race_payload(race_number, race_cache_db)
        if payload:
            return payload

    from race_agent_dashboard import build_race_payload, load_csv_index, load_query_map

    load_csv_index()
    load_query_map()
    print(f"Race #{race_number}: fetching agent payload from API ...", flush=True)
    payload = build_race_payload(race_number, top_agents=40, fetch_all=True)
    if is_cacheable_status((payload.get("race") or {}).get("status")):
        save_race_payload(payload, race_cache_db)
    return payload


def extract_failed_recommendations(
    race_numbers: list[int],
    db_path: Path,
    *,
    race_cache_db: Path = RACE_AGENT_DB,
    csv_path: Path = DEFAULT_CSV,
    query_map_path: Path = DEFAULT_QUERY_MAP,
    min_interval: float = 0.15,
    max_unique_per_query: int = 3,
    max_attempts_per_query: int = 40,
    force_refresh_payload: bool = False,
) -> dict:
    """Extract wrong recommendations, stopping after max_unique_per_query distinct sets per query."""
    csv_index = _load_csv_index(csv_path)
    query_map = _load_query_map(query_map_path)
    all_tasks: list[dict] = []
    race_meta: dict[int, dict] = {}

    for race_number in race_numbers:
        payload = load_race_payload(
            race_number,
            race_cache_db,
            force_refresh=force_refresh_payload,
        )
        if not payload:
            print(f"Race #{race_number}: skipped (no payload)", flush=True)
            continue
        tasks = _collect_tasks(payload, race_number, csv_index, query_map)
        all_tasks.extend(tasks)
        race_meta[race_number] = {
            "race_id": payload["race"]["race_id"],
            "task_count": len(tasks),
        }
        print(f"Race #{race_number}: {len(tasks)} failed agent-problem pairs", flush=True)

    if not all_tasks:
        return {"added": 0, "tasks": 0, "errors": 0, "log_fetches": 0, "races": race_meta}

    tasks_by_race: dict[int, list[dict]] = defaultdict(list)
    for task in all_tasks:
        tasks_by_race[task["race_number"]].append(task)

    problem_maps: dict[str, dict[str, dict]] = {}
    for race_number, race_tasks in sorted(tasks_by_race.items()):
        race_id = race_meta[race_number]["race_id"]
        aids = sorted({t["agent_version_id"] for t in race_tasks})
        print(f"Race #{race_number}: loading problems for {len(aids)} agents (sequential) ...", flush=True)
        for idx, aid in enumerate(aids, 1):
            if idx % 25 == 0:
                print(f"  agents {idx}/{len(aids)}", flush=True)
            try:
                problem_maps[aid] = _fetch_agent_problems(race_id, aid, min_interval=min_interval)
            except Exception as exc:
                print(f"  warn agent {aid[:8]}: {exc}", flush=True)
                problem_maps[aid] = {}

    query_groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for task in all_tasks:
        query_groups[(task["race_number"], task["query"])].append(task)

    added = 0
    errors = 0
    log_fetches = 0
    total_queries = len(query_groups)
    print(
        f"Extracting up to {max_unique_per_query} unique failures "
        f"from {total_queries} queries ...",
        flush=True,
    )

    for qidx, ((race_number, query), group_tasks) in enumerate(sorted(query_groups.items()), 1):
        if qidx % 10 == 0:
            print(
                f"  queries {qidx}/{total_queries} added={added} log_fetches={log_fetches}",
                flush=True,
            )
        correct = group_tasks[0]["correct_answer"]
        correct_key = _ids_key(correct)
        seen_wrong: set[str] = set()
        attempts = 0

        for task in group_tasks:
            if len(seen_wrong) >= max_unique_per_query:
                break
            if attempts >= max_attempts_per_query:
                break
            attempts += 1

            aid = task["agent_version_id"]
            pid = task["problem_id"]
            problem = problem_maps.get(aid, {}).get(pid)
            if not problem:
                continue
            vrs = problem.get("validator_results") or []
            if not vrs:
                continue
            eval_run_id = vrs[0].get("eval_run_id")
            if not eval_run_id:
                continue

            try:
                steps = _fetch_logs(aid, eval_run_id, pid, min_interval=min_interval)
                log_fetches += 1
                recommended = extract_final_product_ids(steps) or ""
            except Exception:
                errors += 1
                continue

            rec_key = _ids_key(recommended)
            if not rec_key or rec_key == correct_key or rec_key in seen_wrong:
                continue

            seen_wrong.add(rec_key)
            add_failed_attempt(
                {
                    "race_number": race_number,
                    "query": query,
                    "query_code": task.get("query_code"),
                    "category": task.get("category"),
                    "agent_name": task.get("agent_name"),
                    "recommended_product_ids": recommended,
                    "source": "race_log",
                },
                db_path,
            )
            added += 1

    return {
        "added": added,
        "tasks": len(all_tasks),
        "errors": errors,
        "log_fetches": log_fetches,
        "queries": total_queries,
        "races": race_meta,
    }
