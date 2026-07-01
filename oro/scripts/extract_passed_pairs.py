#!/usr/bin/env python3
"""Extract passed {query, recommend_product} pairs from race eval trajectories."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    races: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        data = get_json(f"{BASE_URL}/v1/public/races/history?limit=100&offset={offset}")
        total = data["total"]
        batch = data.get("races") or []
        if not batch:
            break
        races.extend(batch)
        offset += len(batch)
        if len(batch) < 100:
            break
    return races


def fetch_logs(agent_version_id: str, eval_run_id: str, problem_id: str) -> list:
    meta = post_json(
        f"{BASE_URL}/v1/public/artifacts/download-url",
        {
            "artifact_type": "EVAL_PROBLEM_LOGS",
            "agent_version_id": agent_version_id,
            "eval_run_id": eval_run_id,
            "problem_id": problem_id,
        },
        timeout=60,
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
    if ids:
        return ",".join(ids)
    return None


def extract_query(steps: list) -> str | None:
    for step in steps:
        q = step.get("extra_info", {}).get("query")
        if q:
            return q
    return None


def process_race(race: dict, sleep: float) -> list[dict]:
    race_number = race["race_number"]
    race_id = race["race_id"]
    agent_version_id = race["winner_agent_version_id"]
    agent_name = race.get("winner_agent_name")

    url = (
        f"{BASE_URL}/v1/public/agent-versions/{agent_version_id}/problems"
        f"?phase=RACE&race_id={race_id}"
    )
    problems = get_json(url).get("problems") or []
    rows: list[dict] = []

    for p in problems:
        success_vrs = [
            vr for vr in p.get("validator_results") or [] if vr.get("status") == "SUCCESS"
        ]
        if not success_vrs:
            continue
        eval_run_id = success_vrs[0]["eval_run_id"]
        problem_id = p["problem_id"]
        try:
            steps = fetch_logs(agent_version_id, eval_run_id, problem_id)
            query = extract_query(steps)
            rec = extract_final_product_ids(steps)
            if not query or not rec:
                rows.append(
                    {
                        "race_number": race_number,
                        "race_id": race_id,
                        "agent_name": agent_name,
                        "agent_version_id": agent_version_id,
                        "eval_run_id": eval_run_id,
                        "problem_id": problem_id,
                        "category": p.get("category"),
                        "status": "SUCCESS",
                        "query": query,
                        "recommend_product": rec,
                        "error": "missing query or recommend_product",
                    }
                )
            else:
                rows.append(
                    {
                        "race_number": race_number,
                        "race_id": race_id,
                        "agent_name": agent_name,
                        "agent_version_id": agent_version_id,
                        "eval_run_id": eval_run_id,
                        "problem_id": problem_id,
                        "category": p.get("category"),
                        "status": "SUCCESS",
                        "query": query,
                        "recommend_product": rec,
                    }
                )
        except urllib.error.HTTPError as exc:
            rows.append(
                {
                    "race_number": race_number,
                    "problem_id": problem_id,
                    "category": p.get("category"),
                    "status": "SUCCESS",
                    "error": f"HTTP {exc.code}",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "race_number": race_number,
                    "problem_id": problem_id,
                    "category": p.get("category"),
                    "status": "SUCCESS",
                    "error": str(exc),
                }
            )
        time.sleep(sleep)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-race", type=int, default=40)
    parser.add_argument("--to-race", type=int, default=50)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "data"
        / "races"
        / "races_40_50_passed_pairs.json",
    )
    parser.add_argument("--sleep", type=float, default=0.12)
    args = parser.parse_args()

    history = fetch_race_history()
    targets = [
        r
        for r in history
        if args.from_race <= r.get("race_number", 0) <= args.to_race
        and r.get("status") == "RACE_COMPLETE"
        and r.get("winner_agent_version_id")
    ]
    targets.sort(key=lambda r: r["race_number"])

    all_rows: list[dict] = []
    for race in targets:
        n = race["race_number"]
        print(f"Race #{n} ({race.get('winner_agent_name')}) ...", flush=True)
        rows = process_race(race, args.sleep)
        ok = [r for r in rows if r.get("query") and r.get("recommend_product")]
        print(f"  passed extracted: {len(ok)} / {len(rows)}", flush=True)
        all_rows.extend(rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(all_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    ok_rows = [r for r in all_rows if r.get("query") and r.get("recommend_product")]
    print(f"\nSaved {len(all_rows)} rows ({len(ok_rows)} complete) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
