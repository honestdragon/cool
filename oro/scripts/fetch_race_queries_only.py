#!/usr/bin/env python3
"""Fetch query text for the current (or specified) race and append rows to the CSV.

Query-only mode: never fetches answers from eval logs and never clears existing
correct_answer / answer_agent on any row. Refuses to write if answered row count
would decrease.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import shutil
import sys
import time
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ORO_RACE = Path(__file__).resolve().parents[1] / "oro_race"
sys.path.insert(0, str(ORO_RACE))

from query_codec import encode_query  # noqa: E402
from view_race_queries import (  # noqa: E402
    normalize_query,
    next_row_id,
    read_csv_rows,
    sync_appeared_race_numbers,
    sync_query_frequency,
    write_csv_rows,
)

BASE_URL = "https://api.oroagents.com"
VALID_CATEGORIES = {"Product", "Shop", "Voucher"}


def get_json(url: str, min_interval: float) -> dict | list:
    time.sleep(min_interval)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 1.0
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 502, 503, 504) and attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise


def post_json(url: str, body: dict, min_interval: float) -> dict:
    time.sleep(min_interval)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    delay = 1.0
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 502, 503, 504) and attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise


def fetch_logs(
    agent_version_id: str,
    eval_run_id: str,
    problem_id: str,
    min_interval: float,
) -> list:
    meta = post_json(
        f"{BASE_URL}/v1/public/artifacts/download-url",
        {
            "artifact_type": "EVAL_PROBLEM_LOGS",
            "agent_version_id": agent_version_id,
            "eval_run_id": eval_run_id,
            "problem_id": problem_id,
        },
        min_interval,
    )
    time.sleep(min_interval)
    with urllib.request.urlopen(meta["download_url"], timeout=120) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode())
    except Exception:
        return json.loads(gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode())


def extract_query(steps: list) -> str | None:
    for step in steps:
        q = step.get("extra_info", {}).get("query")
        if q:
            return normalize_query(q)
    return None


def answered_count(rows: list[dict]) -> int:
    return sum(1 for row in rows if (row.get("correct_answer") or "").strip())


def prior_known_for_query(rows: list[dict], query: str, race_number: int) -> tuple[str, str]:
    """Most recent earlier race row with a known answer for this query."""
    target = normalize_query(query)
    best_rn = -1
    answer = ""
    agent = ""
    for row in rows:
        if normalize_query(row["query"]) != target:
            continue
        rn = int(row["race_number"])
        if rn >= race_number:
            continue
        ans = (row.get("correct_answer") or "").strip()
        if not ans:
            continue
        if rn > best_rn:
            best_rn = rn
            answer = ans
            agent = (row.get("answer_agent") or "").strip()
    return answer, agent


def fetch_queries_for_race(
    race_number: int,
    race_id: str,
    agents: list[tuple[str, str]],
    min_interval: float,
    workers: int,
) -> dict[str, dict]:
    ref_name, ref_id = agents[0]
    problems = get_json(
        f"{BASE_URL}/v1/public/agent-versions/{ref_id}/problems"
        f"?phase=RACE&race_id={race_id}",
        min_interval,
    ).get("problems") or []
    print(f"Reference agent {ref_name}: {len(problems)} problems", flush=True)

    agent_maps: dict[str, dict[str, dict]] = {ref_id: {p["problem_id"]: p for p in problems}}
    max_agents = min(len(agents), 50)
    for name, aid in agents[:max_agents]:
        if aid in agent_maps:
            continue
        probs = get_json(
            f"{BASE_URL}/v1/public/agent-versions/{aid}/problems"
            f"?phase=RACE&race_id={race_id}",
            min_interval,
        ).get("problems") or []
        agent_maps[aid] = {p["problem_id"]: p for p in probs}
    print(f"Loaded problem maps for {len(agent_maps)} agents", flush=True)

    problem_ids = [p["problem_id"] for p in problems]

    def fetch_one(pid: str) -> tuple[str, str | None, str, str]:
        for name, aid in agents[:max_agents]:
            problem = agent_maps.get(aid, {}).get(pid)
            if not problem:
                continue
            vrs = problem.get("validator_results") or []
            if not vrs:
                continue
            eval_run_id = next(
                (vr["eval_run_id"] for vr in vrs if vr.get("status") == "SUCCESS"),
                vrs[0]["eval_run_id"],
            )
            try:
                steps = fetch_logs(aid, eval_run_id, pid, min_interval)
                query = extract_query(steps)
                if query:
                    return pid, query, problem.get("category") or "", name
            except Exception:
                continue
        return pid, None, "", ""

    query_by_pid: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_one, pid): pid for pid in problem_ids}
        done = 0
        for fut in as_completed(futs):
            done += 1
            pid, query, category, source = fut.result()
            if query:
                query_by_pid[pid] = {"query": query, "category": category, "source": source}
            if done % 20 == 0 or done == len(problem_ids):
                print(
                    f"  progress {done}/{len(problem_ids)} got={len(query_by_pid)}",
                    flush=True,
                )
    return query_by_pid


def append_queries(
    rows: list[dict],
    race_number: int,
    query_by_pid: dict[str, dict],
    copy_known: bool,
) -> int:
    existing_keys = {
        (int(row["race_number"]), normalize_query(row["query"]))
        for row in rows
        if row.get("query")
    }
    added = 0
    next_id = next_row_id(rows)

    for info in query_by_pid.values():
        query = info["query"]
        category = info["category"]
        if category not in VALID_CATEGORIES:
            continue
        key = (race_number, query)
        if key in existing_keys:
            continue

        correct_answer = ""
        answer_agent = ""
        if copy_known:
            correct_answer, answer_agent = prior_known_for_query(rows, query, race_number)

        rows.append(
            {
                "id": str(next_id),
                "race_number": str(race_number),
                "category": category,
                "query": query,
                "query_code": encode_query(query),
                "frequency": "1",
                "appeared_race_numbers": "",
                "correct_answer": correct_answer,
                "answer_agent": answer_agent,
            }
        )
        next_id += 1
        added += 1
        existing_keys.add(key)

    for query in {
        normalize_query(r["query"])
        for r in rows
        if int(r["race_number"]) == race_number
    }:
        sync_query_frequency(rows, query)
    sync_appeared_race_numbers(rows)
    return added


def backfill_known_for_race(rows: list[dict], race_number: int) -> int:
    """Fill empty correct_answer on target-race rows from latest earlier race (never overwrite)."""
    filled = 0
    for row in rows:
        if int(row["race_number"]) != race_number:
            continue
        if (row.get("correct_answer") or "").strip():
            continue
        query = row.get("query") or ""
        answer, agent = prior_known_for_query(rows, query, race_number)
        if not answer:
            continue
        row["correct_answer"] = answer
        if agent:
            row["answer_agent"] = agent
        filled += 1
    return filled


def main() -> int:
    parser = argparse.ArgumentParser(description="Append race queries to CSV (no answer overwrite).")
    parser.add_argument("--race-number", type=int, help="Race number (default: current race)")
    parser.add_argument(
        "--csv",
        type=Path,
        default=ORO_RACE / "race-problems-queries-2026-06-22.csv",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "races" / "race_problem_query_map.json",
    )
    parser.add_argument("--min-interval", type=float, default=0.08)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--copy-known",
        action="store_true",
        help="Copy known answers from latest earlier race (new rows + empty rows in target race)",
    )
    parser.add_argument(
        "--backfill-known-only",
        action="store_true",
        help="Only backfill empty answers on existing target-race rows (no API fetch)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.backfill_known_only and not args.race_number:
        raise SystemExit("--backfill-known-only requires --race-number")

    race_number: int
    query_by_pid: dict[str, dict] = {}

    if args.backfill_known_only:
        race_number = args.race_number
        print(f"Backfill-only mode for race #{race_number}", flush=True)
    else:
        cur = get_json(f"{BASE_URL}/v1/public/races/current", args.min_interval)
        race = cur["race"]
        race_number = args.race_number or int(race["race_number"])
        race_id = race["race_id"]
        qualifiers = cur.get("qualifiers") or []
        if args.race_number and int(race.get("race_number", 0)) != race_number:
            history = get_json(
                f"{BASE_URL}/v1/public/races/history?limit=100&offset=0", args.min_interval
            )
            match = next(
                (r for r in history.get("races") or [] if int(r.get("race_number", 0)) == race_number),
                None,
            )
            if not match:
                raise SystemExit(f"Race #{race_number} not found in history")
            race_id = match["race_id"]
            detail = get_json(f"{BASE_URL}/v1/public/races/{race_id}", args.min_interval)
            qualifiers = detail.get("qualifiers") or []

        print(f"Target race #{race_number} id={race_id}", flush=True)

        scored = [q for q in qualifiers if q.get("race_score") is not None]
        scored.sort(key=lambda q: (-float(q["race_score"]), q.get("agent_name", "")))
        agents = [(q["agent_name"], q["agent_version_id"]) for q in scored]
        if not agents:
            raise SystemExit("No evaluated agents yet — cannot fetch queries from eval logs")

        print(f"Evaluated agents: {len(agents)}", flush=True)

        query_by_pid = fetch_queries_for_race(
            race_number, race_id, agents, args.min_interval, args.workers
        )
        print(f"Got {len(query_by_pid)} queries", flush=True)

        if args.cache:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            cache = {}
            if args.cache.exists():
                cache = json.loads(args.cache.read_text(encoding="utf-8"))
            cache[str(race_number)] = {pid: info["query"] for pid, info in query_by_pid.items()}
            if not args.dry_run:
                args.cache.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"Updated cache: {args.cache}", flush=True)

    rows = read_csv_rows(args.csv)
    before_answered = answered_count(rows)
    added = 0
    if query_by_pid:
        added = append_queries(rows, race_number, query_by_pid, args.copy_known)
    backfilled = 0
    if args.copy_known or args.backfill_known_only:
        backfilled = backfill_known_for_race(rows, race_number)
    after_answered = answered_count(rows)

    if after_answered < before_answered:
        raise SystemExit(
            f"Refusing to write: answered rows would drop {before_answered} -> {after_answered}"
        )

    race_rows = [r for r in rows if int(r["race_number"]) == race_number]
    cats = Counter(r["category"] for r in race_rows)
    print(
        f"added={added} backfilled={backfilled} race_rows={len(race_rows)} "
        f"answered={after_answered} (was {before_answered}) categories={dict(cats)}",
        flush=True,
    )

    if args.dry_run:
        print("Dry run — CSV not written", flush=True)
        return 0

    backup = args.csv.with_suffix(f".csv.bak_race{race_number}")
    if args.csv.exists() and not backup.exists():
        shutil.copy2(args.csv, backup)

    write_csv_rows(args.csv, rows)
    print(f"Updated {args.csv}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
