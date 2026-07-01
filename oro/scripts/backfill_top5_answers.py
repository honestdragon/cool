#!/usr/bin/env python3
"""Backfill correct answers from top-N agents' SUCCESS status for completed races.

The rebuild pipeline only checked the race winner ("king"). Other top agents may
have passed problems the winner failed — this script fills those gaps.
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

from view_race_queries import (  # noqa: E402
    normalize_query,
    read_csv_rows,
    sync_answers_in_rows,
    write_csv_rows,
)

# Reuse fetch helpers from the live-race monitor.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor_race_answers import (  # noqa: E402
    DEFAULT_CACHE,
    build_agent_maps,
    ensure_query_map,
    fetch_for_missing,
    log_line,
    merge_into_csv,
    missing_race_queries,
    resolve_race,
    evaluated_agents,
)
from clean_race_query_answers import (  # noqa: E402
    DEFAULT_CACHE_OUT,
    load_verified_cache,
    save_verified_cache,
    verified_key,
)

DEFAULT_CHECKPOINT = (
    Path(__file__).resolve().parents[1] / "data" / "races" / "race_queries_checkpoint.json"
)


def top_evaluated_agents(
    qualifiers: list[dict],
    *,
    from_rank: int = 1,
    to_rank: int = 5,
) -> list[tuple[str, str, float]]:
    """Return agents ranked from_rank..to_rank (1-indexed, inclusive)."""
    agents = evaluated_agents(qualifiers)
    start = max(0, from_rank - 1)
    end = max(start, to_rank)
    return agents[start:end]


def seed_query_map_from_checkpoint(
    cache_path: Path,
    checkpoint_path: Path,
    race_number: int,
) -> int:
    if not checkpoint_path.exists():
        return 0
    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    mapping = {}
    if cache_path.exists():
        all_maps = json.loads(cache_path.read_text(encoding="utf-8"))
        mapping = dict(all_maps.get(str(race_number)) or {})

    added = 0
    for item in data.values():
        if int(item.get("race_number", 0)) != race_number:
            continue
        pid = item.get("problem_id")
        query = normalize_query(item.get("query") or "")
        if pid and query and pid not in mapping:
            mapping[pid] = query
            added += 1

    if added:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        all_maps = {}
        if cache_path.exists():
            all_maps = json.loads(cache_path.read_text(encoding="utf-8"))
        all_maps[str(race_number)] = mapping
        cache_path.write_text(json.dumps(all_maps, indent=2, ensure_ascii=False), encoding="utf-8")
    return added


def races_with_missing(csv_path: Path, from_race: int, to_race: int) -> list[int]:
    rows = read_csv_rows(csv_path)
    missing: set[int] = set()
    for row in rows:
        rn = int(row["race_number"])
        if rn < from_race or rn > to_race:
            continue
        if row.get("query") and not (row.get("correct_answer") or "").strip():
            missing.add(rn)
    return sorted(missing)


def backfill_race(
    race_number: int,
    csv_path: Path,
    from_rank: int,
    to_rank: int,
    min_interval: float,
    cache_path: Path,
    checkpoint_path: Path,
    log_path: Path | None,
) -> dict:
    race, qualifiers = resolve_race(race_number)
    rn = int(race["race_number"])
    race_id = race["race_id"]
    status = race.get("status")
    agents = top_evaluated_agents(qualifiers, from_rank=from_rank, to_rank=to_rank)
    if not agents:
        return {
            "race_number": rn,
            "status": status,
            "agents_checked": 0,
            "csv_updated_rows": 0,
            "csv_answered": 0,
            "csv_total": 0,
            "csv_missing": 0,
        }

    seeded = seed_query_map_from_checkpoint(cache_path, checkpoint_path, rn)
    if seeded:
        log_line(log_path, "query_map_seeded", race_number=rn, added=seeded)

    problem_ids, agent_maps, success_counts = build_agent_maps(race_id, agents, min_interval)
    query_map = ensure_query_map(
        rn, problem_ids, agents, agent_maps, cache_path, min_interval, log_path
    )
    missing_left = missing_race_queries(csv_path, rn)
    if not missing_left:
        rows = read_csv_rows(csv_path)
        total = sum(1 for r in rows if r.get("race_number") == str(rn))
        answered = total - len(missing_left)
        return {
            "race_number": rn,
            "status": status,
            "agents_checked": len(agents),
            "agent_names": [a[0] for a in agents],
            "success_counts": success_counts,
            "unique_passed_answers": 0,
            "csv_updated_rows": 0,
            "csv_answered": answered,
            "csv_total": total,
            "csv_missing": len(missing_left),
        }

    answers = fetch_for_missing(
        problem_ids,
        agents,
        agent_maps,
        query_map,
        set(missing_left),
        min_interval,
        log_path,
    )
    updated, answered, total = merge_into_csv(csv_path, rn, answers)

    return {
        "race_number": rn,
        "status": status,
        "agents_checked": len(agents),
        "agent_names": [a[0] for a in agents],
        "success_counts": success_counts,
        "unique_passed_answers": len(answers),
        "csv_updated_rows": updated,
        "csv_answered": answered,
        "csv_total": total,
        "csv_missing": total - answered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill correct answers from top-N agents for completed races"
    )
    parser.add_argument("--from-race", type=int, default=1)
    parser.add_argument("--to-race", type=int, default=70)
    parser.add_argument(
        "--top-agents",
        type=int,
        default=None,
        help="Shortcut: search agents ranked 1..N (overrides --agent-from-rank/--agent-to-rank)",
    )
    parser.add_argument(
        "--agent-from-rank",
        type=int,
        default=6,
        help="First agent rank to search, 1-indexed (default: 6)",
    )
    parser.add_argument(
        "--agent-to-rank",
        type=int,
        default=15,
        help="Last agent rank to search, inclusive (default: 15)",
    )
    parser.add_argument(
        "--verified-cache",
        type=Path,
        default=DEFAULT_CACHE_OUT,
        help="Update verified SUCCESS cache when new answers are found",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ORO_RACE / "race-problems-queries-2026-06-22.csv",
    )
    parser.add_argument("--min-interval", type=float, default=0.08)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--race", type=int, default=None, help="Single race number only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    if args.top_agents is not None:
        from_rank, to_rank = 1, args.top_agents
    else:
        from_rank, to_rank = args.agent_from_rank, args.agent_to_rank
    if from_rank < 1 or to_rank < from_rank:
        raise SystemExit(f"Invalid agent rank range: {from_rank}..{to_rank}")

    if args.race is not None:
        target_races = [args.race]
    else:
        target_races = races_with_missing(args.csv, args.from_race, args.to_race)

    rows_before = read_csv_rows(args.csv)
    missing_before = sum(1 for r in rows_before if not (r.get("correct_answer") or "").strip())
    print(
        f"Backfilling {len(target_races)} races with missing answers "
        f"(agents rank {from_rank}-{to_rank}, missing={missing_before})",
        flush=True,
    )

    if args.dry_run:
        for rn in target_races[:10]:
            print(f"  would process race {rn}", flush=True)
        if len(target_races) > 10:
            print(f"  ... and {len(target_races) - 10} more", flush=True)
        return 0

    backup = args.csv.with_suffix(".csv.bak_top5")
    if args.csv.exists() and not backup.exists():
        shutil.copy2(args.csv, backup)

    verified = load_verified_cache(args.verified_cache)
    total_updated = 0
    total_new_answers = 0
    for i, rn in enumerate(target_races, 1):
        print(f"\n[{i}/{len(target_races)}] Race #{rn} ...", flush=True)
        try:
            summary = backfill_race(
                rn,
                args.csv,
                from_rank,
                to_rank,
                args.min_interval,
                args.cache,
                args.checkpoint,
                log_path=None,
            )
        except Exception as exc:
            print(f"  ERROR race {rn}: {exc}", flush=True)
            time.sleep(15)
            continue
        total_updated += summary.get("csv_updated_rows", 0)
        new_count = summary.get("unique_passed_answers", 0)
        total_new_answers += new_count
        if new_count:
            rows = read_csv_rows(args.csv)
            for row in rows:
                if int(row["race_number"]) != rn:
                    continue
                answer = (row.get("correct_answer") or "").strip()
                if not answer:
                    continue
                query = normalize_query(row["query"])
                key = verified_key(rn, query)
                verified[key] = {
                    "race_number": rn,
                    "query": query,
                    "correct_answer": answer,
                    "answer_agent": (row.get("answer_agent") or "").strip(),
                    "source": f"api_success_rank_{from_rank}_{to_rank}",
                }
            save_verified_cache(args.verified_cache, verified)
        print(
            f"  agents={summary.get('agent_names')} "
            f"success={summary.get('success_counts')} "
            f"new={summary.get('unique_passed_answers')} "
            f"answered={summary.get('csv_answered')}/{summary.get('csv_total')}",
            flush=True,
        )
        time.sleep(0.5)

    rows_after = read_csv_rows(args.csv)
    synced = sync_answers_in_rows(rows_after)
    missing_after = sum(1 for r in rows_after if not (r.get("correct_answer") or "").strip())
    if synced:
        write_csv_rows(args.csv, rows_after)

    print(
        f"\nDone: filled {total_new_answers} new answers, updated {total_updated} rows, "
        f"synced {synced} shared-query rows",
        flush=True,
    )
    print(
        f"Missing before: {missing_before} -> after: {missing_after} "
        f"({len(rows_after) - missing_after}/{len(rows_after)} answered)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
