#!/usr/bin/env python3
"""Backfill answer_agent in race-problems-queries CSV from checkpoint data."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ORO_RACE = Path(__file__).resolve().parents[1] / "oro_race"
sys.path.insert(0, str(ORO_RACE))

from view_race_queries import (  # noqa: E402
    normalize_query,
    read_csv_rows,
    sync_answers_in_rows,
    write_csv_rows,
)

DEFAULT_CSV = ORO_RACE / "race-problems-queries-2026-06-22.csv"
DEFAULT_CHECKPOINT = Path(__file__).resolve().parents[1] / "data" / "races" / "race_queries_checkpoint.json"


def load_agent_lookup(checkpoint_path: Path) -> tuple[dict[tuple[int, str], str], dict[tuple[str, str], str]]:
    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    by_race_query: dict[tuple[int, str], str] = {}
    by_query_answer: dict[tuple[str, str], str] = {}
    answer_agents: dict[tuple[str, str], Counter[str]] = {}

    for item in data.values():
        query = normalize_query(item.get("query") or "")
        agent = (item.get("agent_name") or "").strip()
        answer = (item.get("correct_answer") or "").strip()
        if not query or not agent:
            continue
        by_race_query[(int(item["race_number"]), query)] = agent
        if answer:
            answer_agents.setdefault((query, answer), Counter())[agent] += 1

    for key, counter in answer_agents.items():
        by_query_answer[key] = counter.most_common(1)[0][0]

    return by_race_query, by_query_answer


def backfill_rows(
    rows: list[dict],
    by_race_query: dict[tuple[int, str], str],
    by_query_answer: dict[tuple[str, str], str],
) -> int:
    filled = 0
    for row in rows:
        if (row.get("answer_agent") or "").strip():
            continue
        if not (row.get("correct_answer") or "").strip():
            continue
        query = normalize_query(row["query"])
        race_number = int(row["race_number"])
        answer = (row.get("correct_answer") or "").strip()
        agent = by_race_query.get((race_number, query)) or by_query_answer.get((query, answer))
        if not agent:
            continue
        row["answer_agent"] = agent
        filled += 1
    return filled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")
    if not args.checkpoint.exists():
        raise SystemExit(f"Checkpoint not found: {args.checkpoint}")

    by_race_query, by_query_answer = load_agent_lookup(args.checkpoint)
    rows = read_csv_rows(args.csv)
    for row in rows:
        row["query"] = normalize_query(row["query"])

    filled = backfill_rows(rows, by_race_query, by_query_answer)
    synced = sync_answers_in_rows(rows)
    answered = sum(1 for row in rows if (row.get("correct_answer") or "").strip())
    with_agent = sum(
        1
        for row in rows
        if (row.get("correct_answer") or "").strip() and (row.get("answer_agent") or "").strip()
    )

    print(f"Filled answer_agent on {filled} rows")
    print(f"Synced shared query metadata on {synced} rows")
    print(f"Answered with solver: {with_agent} / {answered}")

    if args.dry_run:
        return 0

    if filled or synced:
        backup = args.csv.with_suffix(".csv.bak_agents")
        if args.csv.exists():
            shutil.copy2(args.csv, backup)
        write_csv_rows(args.csv, rows)
        print(f"Updated {args.csv}")
    else:
        print("No changes needed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
