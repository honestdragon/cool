#!/usr/bin/env python3
"""Fetch Oro (SN15) race data from the public Backend API.

Publicly available:
  - Race history and metadata (winner, scores, dates)
  - Race qualifiers and rankings
  - Per-race problem IDs, categories, and validator scores
  - Qualifying suite problems (full metadata: query, reward, voucher)

NOT publicly available (hidden race bank):
  - Full race problem metadata (query, reward, voucher, ground truth)
  - Requires a registered validator wallet:
    GET /v1/validator/evaluation-runs/{eval_run_id}/problems

Usage:
  python scripts/fetch_race_data.py
  python scripts/fetch_race_data.py --output-dir data/races --include-qualifying
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.oroagents.com"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "races"


def _get_json(url: str, timeout: int = 60, retries: int = 5) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt + 1 < retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            raise


def fetch_race_history(base_url: str, limit: int = 100) -> list[dict]:
    races: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        params = urllib.parse.urlencode({"limit": limit, "offset": offset})
        data = _get_json(f"{base_url}/v1/public/races/history?{params}")
        total = data["total"]
        batch = data.get("races") or []
        if not batch:
            break
        races.extend(batch)
        offset += len(batch)
        if len(batch) < limit:
            break
        time.sleep(0.1)
    return races


def fetch_race_detail(base_url: str, race_id: str) -> dict:
    return _get_json(f"{base_url}/v1/public/races/{race_id}")


def fetch_race_problems(
    base_url: str, agent_version_id: str, race_id: str
) -> list[dict]:
    params = urllib.parse.urlencode(
        {"phase": "RACE", "race_id": race_id}
    )
    url = (
        f"{base_url}/v1/public/agent-versions/{agent_version_id}/problems"
        f"?{params}"
    )
    data = _get_json(url)
    return data.get("problems") or []


def fetch_qualifying_problems(base_url: str, suite_id: int) -> dict:
    return _get_json(f"{base_url}/v1/public/suites/{suite_id}/problems")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Oro race data")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write JSON output",
    )
    parser.add_argument(
        "--include-qualifying",
        action="store_true",
        help="Also fetch public qualifying suite problems (full metadata)",
    )
    parser.add_argument(
        "--skip-problems",
        action="store_true",
        help="Skip per-race problem ID fetch (faster, metadata only)",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching race history from {base_url} ...")
    try:
        races = fetch_race_history(base_url)
    except urllib.error.URLError as exc:
        print(f"Failed to fetch race history: {exc}", file=sys.stderr)
        return 1

    history_path = out_dir / "race_history.json"
    history_path.write_text(json.dumps(races, indent=2), encoding="utf-8")
    print(f"Saved {len(races)} races -> {history_path}")

    all_race_data: list[dict] = []
    for i, race in enumerate(races, 1):
        race_id = race["race_id"]
        race_number = race.get("race_number", "?")
        print(f"[{i}/{len(races)}] Race #{race_number} ({race_id}) ...")

        try:
            detail = fetch_race_detail(base_url, race_id)
        except urllib.error.URLError as exc:
            print(f"  WARN: detail fetch failed: {exc}", file=sys.stderr)
            detail = {"race": race, "qualifiers": [], "error": str(exc)}

        entry: dict = {
            "race": detail.get("race", race),
            "qualifiers": detail.get("qualifiers", []),
            "problems": [],
        }

        winner_id = race.get("winner_agent_version_id")
        if not args.skip_problems and winner_id:
            try:
                entry["problems"] = fetch_race_problems(
                    base_url, winner_id, race_id
                )
                print(f"  {len(entry['problems'])} problems (IDs + scores only)")
            except urllib.error.URLError as exc:
                print(f"  WARN: problems fetch failed: {exc}", file=sys.stderr)
                entry["problems_error"] = str(exc)
        elif not winner_id:
            print("  no winner yet, skipping problems")

        race_path = out_dir / f"race_{race_number:03d}_{race_id}.json"
        race_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        all_race_data.append(entry)
        time.sleep(0.25)

    combined_path = out_dir / "all_races.json"
    combined_path.write_text(
        json.dumps(all_race_data, indent=2), encoding="utf-8"
    )
    print(f"Combined dump -> {combined_path}")

    if args.include_qualifying:
        current = _get_json(f"{base_url}/v1/public/suites/current")
        suite_id = current["suite_id"]
        print(f"Fetching qualifying suite {suite_id} problems ...")
        qualifying = fetch_qualifying_problems(base_url, suite_id)
        qual_path = out_dir / f"qualifying_suite_{suite_id}.json"
        qual_path.write_text(json.dumps(qualifying, indent=2), encoding="utf-8")
        n = len(qualifying.get("problems") or [])
        print(f"Saved {n} qualifying problems -> {qual_path}")

    print("\nDone.")
    print(
        "\nNote: race problem IDs and validator scores are public, but "
        "query/reward/voucher ground truth is in the hidden race bank.\n"
        "Validators can fetch full metadata via:\n"
        "  GET /v1/validator/evaluation-runs/{eval_run_id}/problems"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
