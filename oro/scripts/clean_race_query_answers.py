#!/usr/bin/env python3
"""Keep only race-verified SUCCESS answers in the queries CSV.

Removes answers from failed runs, winner trajectories without SUCCESS, suite
guesses, and cross-race duplicate propagation. Each row keeps an answer only
when that exact race+query pair has validator status SUCCESS.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ORO_RACE = Path(__file__).resolve().parents[1] / "oro_race"
sys.path.insert(0, str(ORO_RACE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from view_race_queries import (  # noqa: E402
    normalize_query,
    read_csv_rows,
    sync_appeared_race_numbers,
    write_csv_rows,
)
from monitor_race_answers import (  # noqa: E402
    DEFAULT_CACHE,
    build_agent_maps,
    ensure_query_map,
    evaluated_agents,
    fetch_passed_answers,
    iter_success_tasks,
    log_line,
    resolve_race,
)

DEFAULT_CSV = ORO_RACE / "race-problems-queries-2026-06-22.csv"
DEFAULT_CHECKPOINT = Path(__file__).resolve().parents[1] / "data" / "races" / "race_queries_checkpoint.json"
DEFAULT_CACHE_OUT = Path(__file__).resolve().parents[1] / "data" / "races" / "verified_race_answers.json"


def verified_key(race_number: int, query: str) -> str:
    return f"{race_number}\t{normalize_query(query)}"


def load_verified_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "entries" in data:
        return data["entries"]
    return data if isinstance(data, dict) else {}


def save_verified_cache(path: Path, entries: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entry_count": len(entries),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_verified_from_checkpoint(checkpoint_path: Path) -> dict[str, dict]:
    if not checkpoint_path.exists():
        return {}
    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    entries: dict[str, dict] = {}
    for item in data.values():
        if not item.get("passed") or item.get("validator_status") != "SUCCESS":
            continue
        query = normalize_query(item.get("query") or "")
        answer = (item.get("correct_answer") or "").strip()
        agent = (item.get("agent_name") or "").strip()
        if not query or not answer:
            continue
        race_number = int(item["race_number"])
        key = verified_key(race_number, query)
        entries[key] = {
            "race_number": race_number,
            "query": query,
            "correct_answer": answer,
            "answer_agent": agent,
            "source": "checkpoint",
        }
    return entries


def fetch_verified_for_race(
    race_number: int,
    min_interval: float,
    query_cache_path: Path,
    log_path: Path | None,
    *,
    top_agents: int = 5,
) -> dict[str, dict]:
    race, qualifiers = resolve_race(race_number)
    rn = int(race["race_number"])
    agents = evaluated_agents(qualifiers)[:top_agents]
    if not agents:
        log_line(log_path, "fetch_race_skip", race_number=rn, reason="no_evaluated_agents")
        return {}

    problem_ids, agent_maps, success_counts = build_agent_maps(
        race["race_id"], agents, min_interval
    )
    ensure_query_map(
        rn, problem_ids, agents, agent_maps, query_cache_path, min_interval, log_path
    )
    tasks = iter_success_tasks(problem_ids, agents, agent_maps, one_per_problem=True)
    answers_by_query = fetch_passed_answers(tasks, min_interval, log_path)

    entries: dict[str, dict] = {}
    for query, hit in answers_by_query.items():
        answer = (hit.get("correct_answer") or "").strip()
        if not answer:
            continue
        key = verified_key(rn, query)
        entries[key] = {
            "race_number": rn,
            "query": normalize_query(query),
            "correct_answer": answer,
            "answer_agent": (hit.get("agent_name") or "").strip(),
            "source": "api_success",
        }

    log_line(
        log_path,
        "fetch_race_done",
        race_number=rn,
        problems=len(problem_ids),
        success_tasks=len(tasks),
        verified=len(entries),
        top_success=max(success_counts.values()) if success_counts else 0,
    )
    return entries


def apply_verified_to_rows(rows: list[dict], verified: dict[str, dict]) -> dict[str, int]:
    stats = {"kept": 0, "set": 0, "cleared": 0, "unchanged": 0}
    for row in rows:
        race_number = int(row["race_number"])
        query = normalize_query(row["query"])
        key = verified_key(race_number, query)
        hit = verified.get(key)
        cur_answer = (row.get("correct_answer") or "").strip()
        cur_agent = (row.get("answer_agent") or "").strip()

        if hit:
            new_answer = hit["correct_answer"]
            new_agent = hit.get("answer_agent") or ""
            if cur_answer == new_answer and cur_agent == new_agent:
                stats["unchanged"] += 1
            elif cur_answer == new_answer:
                stats["kept"] += 1
            else:
                stats["set"] += 1
            row["correct_answer"] = new_answer
            row["answer_agent"] = new_agent
        else:
            if cur_answer or cur_agent:
                stats["cleared"] += 1
            row["correct_answer"] = ""
            row["answer_agent"] = ""
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep only SUCCESS-verified race answers")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_OUT)
    parser.add_argument("--from-race", type=int, default=None)
    parser.add_argument("--to-race", type=int, default=None)
    parser.add_argument("--min-interval", type=float, default=0.08)
    parser.add_argument("--top-agents", type=int, default=5, help="Top N race agents to scan for SUCCESS")
    parser.add_argument("--no-fetch", action="store_true", help="Use checkpoint + cache only")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    rows = read_csv_rows(args.csv)
    for row in rows:
        row["query"] = normalize_query(row["query"])

    race_numbers = sorted({int(row["race_number"]) for row in rows})
    if args.from_race is not None:
        race_numbers = [rn for rn in race_numbers if rn >= args.from_race]
    if args.to_race is not None:
        race_numbers = [rn for rn in race_numbers if rn <= args.to_race]

    verified = load_verified_cache(args.cache)
    verified.update(load_verified_from_checkpoint(args.checkpoint))
    print(f"Verified entries loaded: {len(verified)} (cache + checkpoint)")

    if not args.no_fetch:
        rows_by_race: dict[int, int] = {}
        for row in rows:
            rn = int(row["race_number"])
            rows_by_race[rn] = rows_by_race.get(rn, 0) + 1

        for rn in race_numbers:
            cached_for_race = sum(1 for key in verified if key.startswith(f"{rn}\t"))
            need = rows_by_race.get(rn, 0)
            if cached_for_race >= need and need > 0:
                print(f"Race #{rn}: skip fetch ({cached_for_race}/{need} cached)", flush=True)
                continue
            print(f"Race #{rn}: fetching SUCCESS answers (top {args.top_agents}) ...", flush=True)
            try:
                fetched = fetch_verified_for_race(
                    rn,
                    args.min_interval,
                    DEFAULT_CACHE,
                    args.log,
                    top_agents=args.top_agents,
                )
                verified.update(fetched)
                if not args.dry_run:
                    save_verified_cache(args.cache, verified)
            except Exception as exc:
                print(f"  warn race #{rn}: {exc}", flush=True)

    stats = apply_verified_to_rows(rows, verified)
    sync_appeared_race_numbers(rows)

    answered = sum(1 for row in rows if (row.get("correct_answer") or "").strip())
    with_agent = sum(
        1
        for row in rows
        if (row.get("correct_answer") or "").strip() and (row.get("answer_agent") or "").strip()
    )

    print(f"Verified unique race+query pairs: {len(verified)}")
    print(f"CSV rows kept unchanged: {stats['unchanged']}")
    print(f"CSV rows updated to verified: {stats['set'] + stats['kept']}")
    print(f"CSV rows cleared (unverified): {stats['cleared']}")
    print(f"Answered after clean: {answered} / {len(rows)}")
    print(f"With solver: {with_agent} / {answered}")

    if args.dry_run:
        return 0

    backup = args.csv.with_suffix(".csv.bak_unverified")
    if args.csv.exists() and not backup.exists():
        shutil.copy2(args.csv, backup)
    write_csv_rows(args.csv, rows)
    save_verified_cache(args.cache, verified)
    print(f"Updated {args.csv}")
    print(f"Backup of previous CSV: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
