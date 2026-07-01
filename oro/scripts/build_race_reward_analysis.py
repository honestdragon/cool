#!/usr/bin/env python3
"""Build the race reward analysis database from CSV answers, suite rewards, and agent failures."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ORO_ROOT = Path(__file__).resolve().parents[1]
ORO_RACE = ORO_ROOT / "oro_race"
sys.path.insert(0, str(ORO_RACE))
sys.path.insert(0, str(ORO_ROOT / "scripts"))

from query_codec import normalize_query  # noqa: E402
from race_reward_analysis_db import (  # noqa: E402
    DEFAULT_DB,
    add_failed_attempt,
    clear_failed_attempts,
    init_db,
    list_analysis,
    list_failed_attempts,
    upsert_analysis,
    update_failed_products,
)
from fill_race_query_answers import extract_final_product_ids, reward_to_answer  # noqa: E402

DEFAULT_CSV = ORO_RACE / "race-problems-queries-2026-06-22.csv"
DEFAULT_SUITE = ORO_ROOT / "data" / "suites" / "problem_suite_v3.json"
DEFAULT_PROXY = "http://127.0.0.1:8080"
DEFAULT_CATALOG = "http://135.181.3.178:5632"


def norm_ids(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(sorted(part.strip() for part in value.split(",") if part.strip()))


def ids_key(value: str | None) -> str:
    return ",".join(norm_ids(value))


def load_suite_rewards(path: Path) -> dict[str, list | dict]:
    if not path.is_file():
        return {}
    problems = json.loads(path.read_text(encoding="utf-8"))
    lookup: dict[str, list | dict] = {}
    if not isinstance(problems, list):
        return lookup
    for problem in problems:
        query = normalize_query(problem.get("query") or "")
        reward = problem.get("reward")
        if query and reward:
            lookup.setdefault(query, reward)
    return lookup


def load_csv_rows(csv_path: Path) -> list[dict]:
    return list(csv.DictReader(csv_path.open(encoding="utf-8")))


def seed_from_csv(csv_path: Path, suite_path: Path, db_path: Path) -> int:
    suite = load_suite_rewards(suite_path)
    count = 0
    for row in load_csv_rows(csv_path):
        answer = (row.get("correct_answer") or "").strip()
        if not answer:
            continue
        query = row["query"]
        reward = suite.get(normalize_query(query))
        upsert_analysis(
            {
                "race_number": int(row["race_number"]),
                "category": row.get("category"),
                "query_code": row.get("query_code"),
                "query": query,
                "correct_product_ids": answer,
                "correct_reward_json": reward,
            },
            db_path,
        )
        count += 1
    return count


def _product_info_path(catalog_url: str) -> str:
    base = catalog_url.rstrip("/")
    if ":5632" in base or base.endswith("5632"):
        return "/view_product_information"
    return "/search/view_product_information"


def fetch_product_info(product_ids: list[str], catalog_url: str, chunk_size: int = 20) -> list[dict]:
    if not product_ids:
        return []
    path = _product_info_path(catalog_url)
    results: list[dict] = []
    for offset in range(0, len(product_ids), chunk_size):
        chunk = product_ids[offset : offset + chunk_size]
        params = urllib.parse.urlencode({"product_ids": ",".join(chunk)})
        url = f"{catalog_url.rstrip('/')}{path}?{params}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            batch = json.load(resp)
        if isinstance(batch, list):
            results.extend(batch)
        time.sleep(0.05)
    return results


def enrich_product_info(
    db_path: Path,
    catalog_url: str,
    *,
    limit: int | None = None,
    only_with_failures: bool = False,
) -> int:
    init_db(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT id, correct_product_ids, failed_products_json, correct_product_info_json,
               failed_product_info_json
        FROM reward_analysis
    """
    if only_with_failures:
        sql += " WHERE failed_products_json != '[]'"
    sql += " ORDER BY id ASC"
    rows = conn.execute(sql).fetchall()
    conn.close()

    updated = 0
    for row in rows:
        if limit is not None and updated >= limit:
            break
        row = dict(row)
        need_correct = not row.get("correct_product_info_json")
        failed = json.loads(row.get("failed_products_json") or "[]")
        failed_info = json.loads(row.get("failed_product_info_json") or "{}")
        missing_failed_keys = [
            entry.get("product_ids", "")
            for entry in failed
            if entry.get("product_ids") and entry["product_ids"] not in failed_info
        ]
        if not need_correct and not missing_failed_keys:
            continue

        all_ids: list[str] = []
        if need_correct:
            all_ids.extend(norm_ids(row["correct_product_ids"]))
        for key in missing_failed_keys:
            all_ids.extend(norm_ids(key))
        all_ids = list(dict.fromkeys(all_ids))
        if not all_ids:
            continue

        products = fetch_product_info(all_ids, catalog_url)
        by_id = {str(item.get("product_id", "")): item for item in products}

        patch: dict = {"race_number": 0, "query": "", "correct_product_ids": ""}
        if need_correct:
            correct_info = [by_id[pid] for pid in norm_ids(row["correct_product_ids"]) if pid in by_id]
            patch["correct_product_info_json"] = correct_info

        if missing_failed_keys:
            for key in missing_failed_keys:
                failed_info[key] = [by_id[pid] for pid in norm_ids(key) if pid in by_id]

        init_db(db_path)
        import sqlite3 as sq

        with sq.connect(db_path) as conn2:
            sets = ["updated_at = ?"]
            params: list = [time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())]
            if need_correct:
                sets.append("correct_product_info_json = ?")
                params.append(json.dumps(patch.get("correct_product_info_json"), ensure_ascii=False))
            if missing_failed_keys:
                sets.append("failed_product_info_json = ?")
                params.append(json.dumps(failed_info, ensure_ascii=False))
            params.append(int(row["id"]))
            conn2.execute(
                f"UPDATE reward_analysis SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn2.commit()
        updated += 1
        if updated % 25 == 0:
            print(f"  enriched {updated} rows ...", flush=True)
    return updated


def import_agent_results(path: Path, db_path: Path, *, agent_name: str, source: str) -> int:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"{path} must contain a JSON array")
    added = 0
    for row in rows:
        expected = (row.get("expected") or row.get("correct_answer") or "").strip()
        recommended = (row.get("recommended") or row.get("recommend_product") or "").strip()
        query = row.get("query") or ""
        if not query or not recommended:
            continue
        if expected and norm_ids(recommended) == norm_ids(expected):
            continue
        add_failed_attempt(
            {
                "race_number": row.get("race_number"),
                "query": query,
                "query_code": row.get("query_code"),
                "category": row.get("category"),
                "agent_name": row.get("agent_name") or agent_name,
                "recommended_product_ids": recommended,
                "source": source,
            },
            db_path,
        )
        added += 1
    return added


def run_agent_failures(
    agent_path: Path,
    csv_path: Path,
    db_path: Path,
    *,
    race_number: int | None = None,
    limit: int | None = None,
) -> int:
    spec = importlib.util.spec_from_file_location("reward_analysis_agent", agent_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "agent_main"):
        raise SystemExit(f"{agent_path} has no agent_main()")

    rows = load_csv_rows(csv_path)
    rows = [r for r in rows if (r.get("correct_answer") or "").strip()]
    if race_number is not None:
        rows = [r for r in rows if int(r["race_number"]) == race_number]
    if limit is not None:
        rows = rows[:limit]

    added = 0
    for idx, row in enumerate(rows, 1):
        query = row["query"]
        expected = row["correct_answer"].strip()
        label = f"[{idx}/{len(rows)}] {row.get('query_code')} race={row['race_number']}"
        try:
            steps = mod.agent_main({"query": query})
            recommended = extract_final_product_ids(steps) or ""
        except Exception as exc:
            print(f"{label} ERROR: {exc}", flush=True)
            continue
        if norm_ids(recommended) == norm_ids(expected):
            print(f"{label} PASS", flush=True)
            continue
        add_failed_attempt(
            {
                "race_number": int(row["race_number"]),
                "query": query,
                "query_code": row.get("query_code"),
                "category": row.get("category"),
                "agent_name": agent_path.stem,
                "recommended_product_ids": recommended or "(none)",
                "source": "agent_test",
            },
            db_path,
        )
        added += 1
        print(f"{label} FAIL expected={expected} got={recommended or '(none)'}", flush=True)
    return added


def aggregate_failed_attempts(
    db_path: Path,
    *,
    top_n: int = 5,
    min_count: int = 1,
) -> int:
    attempts = list_failed_attempts(db_path)
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for attempt in attempts:
        if not attempt.get("race_number"):
            continue
        grouped[(int(attempt["race_number"]), attempt["query"])].append(attempt)

    updated = 0
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for (race_number, query), items in grouped.items():
        row = conn.execute(
            "SELECT id, correct_product_ids FROM reward_analysis WHERE race_number = ? AND query = ?",
            (race_number, query),
        ).fetchone()
        if not row:
            continue
        correct_key = ids_key(row["correct_product_ids"])
        counter: Counter[str] = Counter()
        sources: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            rec = item["recommended_product_ids"]
            if rec in ("(none)", "") or norm_ids(rec) == norm_ids(row["correct_product_ids"]):
                continue
            counter[rec] += 1
            sources[rec].append(
                {
                    "agent_name": item.get("agent_name"),
                    "source": item.get("source"),
                }
            )
        failed_products = [
            {
                "product_ids": rec,
                "count": count,
                "sources": sources[rec][:5],
            }
            for rec, count in counter.most_common(top_n)
            if count >= min_count
        ]
        if failed_products and update_failed_products(int(row["id"]), failed_products, db_path=db_path):
            updated += 1
    conn.close()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--proxy-url", default=DEFAULT_PROXY, help="Legacy proxy URL")
    parser.add_argument(
        "--catalog-url",
        default=DEFAULT_CATALOG,
        help="Search server for view_product_information (default: 135.181.3.178:5632)",
    )
    parser.add_argument("--seed", action="store_true", help="Load known answers from CSV + suite rewards")
    parser.add_argument("--fetch-products", action="store_true", help="Fetch product info from search proxy")
    parser.add_argument("--fetch-limit", type=int, help="Limit rows to enrich with product info")
    parser.add_argument("--agent-file", type=Path, help="Run agent and record mismatches as failures")
    parser.add_argument("--race", type=int, help="Limit agent run to one race number")
    parser.add_argument("--agent-limit", type=int, help="Limit number of queries for agent run")
    parser.add_argument("--import-results", type=Path, help="Import failures from test_race_agent JSON output")
    parser.add_argument("--import-agent-name", default="imported")
    parser.add_argument("--clear-attempts", action="store_true", help="Clear failed_attempts before import/run")
    parser.add_argument("--aggregate", action="store_true", help="Aggregate failed_attempts into analysis rows")
    parser.add_argument(
        "--top-failures",
        type=int,
        default=3,
        help="Max failed product sets kept per query (default: 3)",
    )
    parser.add_argument(
        "--min-failure-count",
        type=int,
        default=1,
        help="Minimum times a wrong product set must appear (default: 1, no filter)",
    )
    parser.add_argument(
        "--extract-races",
        type=str,
        help="Comma-separated race numbers to extract (e.g. 75,76)",
    )
    parser.add_argument(
        "--extract-all",
        action="store_true",
        help="Extract failed recommendations from all completed races",
    )
    parser.add_argument("--extract-workers", type=int, default=4, help="Unused legacy flag")
    parser.add_argument(
        "--max-attempts-per-query",
        type=int,
        default=40,
        help="Max eval log fetches per query while collecting failures",
    )
    parser.add_argument(
        "--fetch-only-failures",
        action="store_true",
        help="Only fetch product info for rows with typical failures",
    )
    parser.add_argument("--all", action="store_true", help="Run seed + aggregate + fetch-products")
    args = parser.parse_args()

    init_db(args.db)

    if args.all:
        args.seed = True
        args.aggregate = True

    if args.clear_attempts:
        removed = clear_failed_attempts(args.db)
        print(f"Cleared {removed} failed attempts")

    if args.seed:
        count = seed_from_csv(args.csv, args.suite, args.db)
        print(f"Seeded {count} queries with known answers")

    if args.import_results:
        added = import_agent_results(
            args.import_results,
            args.db,
            agent_name=args.import_agent_name,
            source="import",
        )
        print(f"Imported {added} failed attempts from {args.import_results}")

    if args.extract_all or args.extract_races:
        from race_failure_extractor import extract_failed_recommendations, fetch_complete_race_numbers

        if args.extract_all:
            race_numbers = fetch_complete_race_numbers()
            print(f"Extracting from all {len(race_numbers)} completed races ...", flush=True)
        else:
            race_numbers = [int(part.strip()) for part in args.extract_races.split(",") if part.strip()]
        result = extract_failed_recommendations(
            race_numbers,
            args.db,
            max_unique_per_query=args.top_failures,
            max_attempts_per_query=args.max_attempts_per_query,
        )
        print(
            f"Extracted {result['added']} failed recommendations "
            f"from {result.get('log_fetches', result['tasks'])} log fetches "
            f"across {result.get('queries', '?')} queries ({result['errors']} errors)"
        )

    if args.agent_file:
        agent_path = args.agent_file
        if not agent_path.is_absolute():
            agent_path = ORO_ROOT / agent_path
        added = run_agent_failures(
            agent_path,
            args.csv,
            args.db,
            race_number=args.race,
            limit=args.agent_limit,
        )
        print(f"Recorded {added} agent failures")

    if args.aggregate:
        updated = aggregate_failed_attempts(
            args.db,
            top_n=args.top_failures,
            min_count=args.min_failure_count,
        )
        print(f"Aggregated failures into {updated} analysis rows (min_count={args.min_failure_count})")

    if args.fetch_products:
        enriched = enrich_product_info(
            args.db,
            args.catalog_url,
            limit=args.fetch_limit,
            only_with_failures=args.fetch_only_failures,
        )
        print(f"Enriched product info for {enriched} rows")

    summary_rows = list_analysis(args.db)
    with_failures = sum(1 for r in summary_rows if r.get("failed_count", 0) > 0)
    print(f"\nDB ready: {len(summary_rows)} queries, {with_failures} with typical failures")
    print(f"Database: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
