#!/usr/bin/env python3
"""Run an agent module against race queries and compare to known correct answers."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from fill_race_query_answers import extract_final_product_ids  # noqa: E402

DEFAULT_CSV = ROOT / "oro_race" / "race-problems-queries-2026-06-22.csv"


def load_agent(agent_path: Path):
    spec = importlib.util.spec_from_file_location("race_agent_under_test", agent_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "agent_main"):
        raise SystemExit(f"{agent_path} has no agent_main()")
    return mod


def norm_ids(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def load_rows(csv_path: Path, race_number: int | None, query_code: str | None) -> list[dict]:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    if race_number is not None:
        rows = [r for r in rows if int(r["race_number"]) == race_number]
    if query_code:
        rows = [r for r in rows if r["query_code"] == query_code]
    return rows


def compare_row(mod, row: dict, *, ledger_only: bool) -> dict:
    query = row["query"]
    expected = row.get("correct_answer", "").strip()
    ledger = ""
    if hasattr(mod, "_race_lookup_known_answer_csv"):
        ledger = mod._race_lookup_known_answer_csv(query) or ""

    if ledger_only:
        recommended = ledger
        elapsed = 0.0
        error = None
        steps = 0
    else:
        t0 = time.time()
        error = None
        steps_list: list = []
        try:
            steps_list = mod.agent_main({"query": query})
            recommended = extract_final_product_ids(steps_list) or ""
        except Exception as exc:
            recommended = ""
            error = f"{type(exc).__name__}: {exc}"
        elapsed = round(time.time() - t0, 1)
        steps = len(steps_list)

    return {
        "race_number": int(row["race_number"]),
        "query_code": row["query_code"],
        "category": row["category"],
        "query": query,
        "expected": expected,
        "recommended": recommended,
        "ledger_override": ledger,
        "match": norm_ids(recommended) == norm_ids(expected) if expected else None,
        "elapsed_s": elapsed,
        "steps": steps,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-file", default="my_07.py", help="Agent module path")
    parser.add_argument("--race", type=int, help="Race number filter (e.g. 72)")
    parser.add_argument("--query-code", help="Single query_code filter (e.g. th3n)")
    parser.add_argument(
        "--only-with-answers",
        action="store_true",
        help="Skip rows without correct_answer in CSV",
    )
    parser.add_argument(
        "--ledger-only",
        action="store_true",
        help="Only compare _race_lookup_known_answer_csv vs CSV (no agent run)",
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--output", type=Path, help="Write JSON results here")
    args = parser.parse_args()

    agent_path = Path(args.agent_file)
    if not agent_path.is_absolute():
        agent_path = ROOT / agent_path

    mod = load_agent(agent_path)
    rows = load_rows(args.csv, args.race, args.query_code)
    if args.only_with_answers:
        rows = [r for r in rows if r.get("correct_answer", "").strip()]

    if not rows:
        raise SystemExit("No matching queries found")

    results = []
    passed = failed = skipped = 0
    for idx, row in enumerate(rows, 1):
        result = compare_row(mod, row, ledger_only=args.ledger_only)
        results.append(result)
        label = f"[{idx}/{len(rows)}] {result['query_code']} ({result['category']})"
        if result["match"] is None:
            skipped += 1
            print(f"{label} SKIP (no CSV answer)")
            continue
        if result["match"]:
            passed += 1
            print(f"{label} PASS ({result['elapsed_s']}s)")
        else:
            failed += 1
            print(f"{label} FAIL ({result['elapsed_s']}s)")
            print(f"  expected:    {result['expected']}")
            print(f"  recommended: {result['recommended'] or '(none)'}")
            if result["ledger_override"]:
                print(f"  ledger:      {result['ledger_override']}")
            if result["error"]:
                print(f"  error:       {result['error']}")

    print("\n=== SUMMARY ===")
    print(f"Passed:  {passed}")
    print(f"Failed:  {failed}")
    print(f"Skipped: {skipped}")
    print(f"Total:   {len(results)}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
