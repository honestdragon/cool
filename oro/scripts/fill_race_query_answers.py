#!/usr/bin/env python3
"""Fill missing correct_answer values in race-problems-queries CSV."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ORO_RACE = Path(__file__).resolve().parents[1] / "oro_race"
sys.path.insert(0, str(ORO_RACE))

from view_race_queries import (  # noqa: E402
    CSV_FIELDS,
    normalize_query,
    read_csv_rows,
    sync_answers_in_rows,
    write_csv_rows,
)

BASE_URL = "https://api.oroagents.com"


def get_json(url: str, timeout: int = 60) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def post_json(url: str, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_race_history() -> list[dict]:
    data = get_json(f"{BASE_URL}/v1/public/races/history?limit=100&offset=0")
    return data.get("races") or []


def fetch_logs(agent_version_id: str, eval_run_id: str, problem_id: str) -> list:
    meta = post_json(
        f"{BASE_URL}/v1/public/artifacts/download-url",
        {
            "artifact_type": "EVAL_PROBLEM_LOGS",
            "agent_version_id": agent_version_id,
            "eval_run_id": eval_run_id,
            "problem_id": problem_id,
        },
    )
    with urllib.request.urlopen(meta["download_url"], timeout=120) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode())
    except Exception:
        return json.loads(gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode())


def extract_final_product_ids(steps: list) -> str | None:
    full = json.dumps(steps)
    matches = re.findall(
        r'recommend_product["\'\\,\s]*parameters["\'\\,\s]*:\s*\{["\'\\,\s]*product_ids["\'\\,\s]*:\s*["\'\\]([\d,]+)',
        full,
    )
    if matches:
        return matches[-1]

    last = steps[-1]
    think = (last.get("completion", {}).get("message") or {}).get("think") or last.get(
        "completion", {}
    ).get("content", "")

    m = re.search(r"Products:\s*\[([^\]]+)\]", think)
    if m:
        ids = re.findall(r"[\d]{8,12}", m.group(1))
        if ids:
            return ",".join(ids)

    m = re.search(r"Selected product_id=([\d]+)", think)
    if m:
        return m.group(1)

    ids: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"product[_ ]id?\s*([\d]{8,12})", think, re.I):
        pid = m.group(1)
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ",".join(ids) if ids else None


def extract_query(steps: list) -> str | None:
    for step in steps:
        q = step.get("extra_info", {}).get("query")
        if q:
            return q
    return None


def reward_to_answer(reward) -> str | None:
    if reward is None:
        return None
    if isinstance(reward, list):
        ids = []
        for item in reward:
            if isinstance(item, dict) and item.get("product_id"):
                ids.append(str(item["product_id"]))
        return ",".join(ids) if ids else None
    if isinstance(reward, dict) and reward.get("product_id"):
        return str(reward["product_id"])
    return None


def load_suite_answers() -> dict[str, str]:
    suites_dir = Path(__file__).resolve().parents[1] / "data" / "suites"
    lookup: dict[str, str] = {}
    for path in sorted(suites_dir.glob("problem_suite_v3*.json")):
        try:
            problems = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(problems, list):
            continue
        for p in problems:
            q = normalize_query(p.get("query") or "")
            ans = reward_to_answer(p.get("reward"))
            if q and ans:
                lookup.setdefault(q, ans)
    return lookup


def load_extracted_lookup(path: Path) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    if not path.exists():
        return lookup
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        q = normalize_query(row.get("query") or "")
        rec = row.get("recommend_product")
        if q and rec:
            lookup[(str(row["race_number"]), q)] = rec
    return lookup


def fetch_missing_from_races(
    races_by_number: dict[int, dict],
    missing_by_race: dict[int, set[str]],
    lookup: dict[tuple[str, str], str],
    sleep: float,
) -> int:
    added = 0
    for race_number in sorted(missing_by_race):
        race = races_by_number.get(race_number)
        if not race:
            continue
        needed = missing_by_race[race_number]
        if not needed:
            continue

        agent_version_id = race["winner_agent_version_id"]
        race_id = race["race_id"]
        url = (
            f"{BASE_URL}/v1/public/agent-versions/{agent_version_id}/problems"
            f"?phase=RACE&race_id={race_id}"
        )
        problems = get_json(url).get("problems") or []
        print(f"Race #{race_number}: need {len(needed)} queries, scanning {len(problems)} problems ...", flush=True)

        for p in problems:
            vrs = p.get("validator_results") or []
            if not vrs:
                continue
            eval_run_id = vrs[0]["eval_run_id"]
            problem_id = p["problem_id"]
            key_candidate = None
            try:
                steps = fetch_logs(agent_version_id, eval_run_id, problem_id)
                query = extract_query(steps)
                rec = extract_final_product_ids(steps)
                if not query or not rec:
                    continue
                q = normalize_query(query)
                key = (str(race_number), q)
                if key in lookup:
                    continue
                lookup[key] = rec
                added += 1
                if q in needed:
                    needed.discard(q)
            except Exception as exc:
                print(f"  warn {problem_id}: {exc}", flush=True)
            time.sleep(sleep)
            if not needed:
                break
    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--csv",
        type=Path,
        default=ORO_RACE / "race-problems-queries-2026-06-22.csv",
    )
    parser.add_argument(
        "--extracted",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "races" / "races_1_70_passed_pairs.json",
    )
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument("--no-fetch", action="store_true", help="Only use cached/suite data")
    args = parser.parse_args()

    lookup = load_extracted_lookup(args.extracted)
    suite_lookup = load_suite_answers()

    rows = read_csv_rows(args.csv)
    for row in rows:
        row["query"] = normalize_query(row["query"])

    missing_before = sum(1 for r in rows if not (r.get("correct_answer") or "").strip())

    if not args.no_fetch and missing_before:
        races = {r["race_number"]: r for r in fetch_race_history()}
        missing_by_race: dict[int, set[str]] = defaultdict(set)
        for row in rows:
            if (row.get("correct_answer") or "").strip():
                continue
            key = (row["race_number"], row["query"].strip())
            if key in lookup:
                continue
            if row["query"].strip() in suite_lookup:
                continue
            missing_by_race[int(row["race_number"])].add(row["query"].strip())
        added = fetch_missing_from_races(races, missing_by_race, lookup, args.sleep)
        print(f"Fetched {added} new race/query answers from trajectories", flush=True)
        args.extracted.parent.mkdir(parents=True, exist_ok=True)
        merged = []
        seen = set()
        for key, rec in lookup.items():
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "race_number": int(key[0]),
                    "query": key[1],
                    "recommend_product": rec,
                }
            )
        args.extracted.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    filled_race = filled_suite = 0
    for row in rows:
        if (row.get("correct_answer") or "").strip():
            continue
        q = row["query"].strip()
        key = (row["race_number"], q)
        ans = lookup.get(key)
        if ans:
            row["correct_answer"] = ans
            filled_race += 1
            continue
        ans = suite_lookup.get(q)
        if ans:
            row["correct_answer"] = ans
            filled_suite += 1

    synced = sync_answers_in_rows(rows)
    missing_after = sum(1 for r in rows if not (r.get("correct_answer") or "").strip())

    backup = args.csv.with_suffix(".csv.bak")
    if not backup.exists():
        shutil.copy2(args.csv, backup)
    write_csv_rows(args.csv, rows)

    print(f"Missing before: {missing_before}")
    print(f"Filled from race trajectories: {filled_race}")
    print(f"Filled from suite rewards: {filled_suite}")
    print(f"Synced by shared query: {synced}")
    print(f"Missing after: {missing_after}")
    print(f"Total answered: {len(rows) - missing_after} / {len(rows)}")
    print(f"Updated {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
