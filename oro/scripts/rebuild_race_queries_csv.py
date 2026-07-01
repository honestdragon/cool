#!/usr/bin/env python3
"""Rebuild race-problems-queries CSV from RACE-phase eval trajectories.

- Query text from EVAL_PROBLEM_LOGS (race bank).
- correct_answer ONLY when validator status == SUCCESS.
- Multithreaded with checkpoint/resume and retry rounds.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ORO_RACE = Path(__file__).resolve().parents[1] / "oro_race"
sys.path.insert(0, str(ORO_RACE))

from query_codec import encode_query  # noqa: E402
from view_race_queries import (  # noqa: E402
    normalize_query,
    sync_appeared_race_numbers,
    sync_query_frequency,
    write_csv_rows,
)

BASE_URL = "https://api.oroagents.com"

_rate_lock = threading.Lock()
_last_request_at = 0.0
_checkpoint_lock = threading.Lock()


def _throttle(min_interval: float) -> None:
    global _last_request_at
    if min_interval <= 0:
        return
    with _rate_lock:
        now = time.monotonic()
        wait = min_interval - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _request_with_retry(open_fn, retries: int = 8):
    delay = 1.0
    for attempt in range(retries):
        try:
            with open_fn() as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403) and attempt + 1 < retries:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise


def get_json(url: str, timeout: int = 60, min_interval: float = 0.0) -> dict | list:
    _throttle(min_interval)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    return _request_with_retry(lambda: urllib.request.urlopen(req, timeout=timeout))


def post_json(url: str, body: dict, timeout: int = 60, min_interval: float = 0.0) -> dict:
    _throttle(min_interval)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    return _request_with_retry(lambda: urllib.request.urlopen(req, timeout=timeout))


def fetch_race_history(min_interval: float) -> list[dict]:
    data = get_json(f"{BASE_URL}/v1/public/races/history?limit=100&offset=0", min_interval=min_interval)
    return data.get("races") or []


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
        min_interval=min_interval,
    )
    _throttle(min_interval)
    delay = 1.0
    for attempt in range(8):
        try:
            with urllib.request.urlopen(meta["download_url"], timeout=120) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403) and attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
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
    return None


def extract_query(steps: list) -> str | None:
    for step in steps:
        q = step.get("extra_info", {}).get("query")
        if q:
            return normalize_query(q)
    return None


def pick_eval_run(problem: dict) -> tuple[str | None, bool]:
    vrs = problem.get("validator_results") or []
    if not vrs:
        return None, False
    success = next((vr for vr in vrs if vr.get("status") == "SUCCESS"), None)
    if success:
        return success["eval_run_id"], True
    return vrs[0].get("eval_run_id"), False


def task_key(race_number: int, problem_id: str, agent_version_id: str = "") -> str:
    if agent_version_id:
        return f"{race_number}:{problem_id}:{agent_version_id}"
    return f"{race_number}:{problem_id}"


def fetch_race_detail(race_id: str, min_interval: float) -> dict:
    return get_json(f"{BASE_URL}/v1/public/races/{race_id}", min_interval=min_interval)


def top_race_agents(
    race: dict,
    min_interval: float,
    top_n: int,
) -> list[tuple[str, str, float]]:
    """Return up to top_n evaluated agents for a race, ordered by race_score."""
    detail = fetch_race_detail(race["race_id"], min_interval)
    agents: list[tuple[str, str, float]] = []
    for q in detail.get("qualifiers") or []:
        score = q.get("race_score")
        aid = q.get("agent_version_id")
        if score is None or not aid:
            continue
        agents.append((q.get("agent_name") or "?", aid, float(score)))
    agents.sort(key=lambda item: (-item[2], item[0]))
    if agents:
        return agents[:top_n]

    winner_id = race.get("winner_agent_version_id")
    if winner_id:
        return [(race.get("winner_agent_name") or "?", winner_id, 0.0)]
    return []


def process_task(task: dict, min_interval: float) -> dict | None:
    eval_run_id, passed = pick_eval_run(task["problem"])
    if not eval_run_id:
        return None
    steps = fetch_logs(
        task["agent_version_id"],
        eval_run_id,
        task["problem"]["problem_id"],
        min_interval,
    )
    query = extract_query(steps)
    if not query:
        return None
    answer = ""
    if passed:
        rec = extract_final_product_ids(steps)
        if rec:
            answer = rec
    return {
        "race_number": task["race_number"],
        "race_id": task["race_id"],
        "agent_name": task["agent_name"],
        "agent_version_id": task["agent_version_id"],
        "problem_id": task["problem"]["problem_id"],
        "category": task["problem"].get("category") or "",
        "query": query,
        "correct_answer": answer,
        "passed": passed,
    }


def load_checkpoint(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def build_tasks(
    races: list[dict],
    from_race: int,
    to_race: int,
    min_interval: float,
    top_agents: int = 5,
) -> list[dict]:
    tasks: list[dict] = []
    for race in races:
        rn = int(race["race_number"])
        if rn < from_race or rn > to_race:
            continue
        agents = top_race_agents(race, min_interval, top_agents)
        if not agents:
            continue
        ref_id = agents[0][1]
        problems = get_json(
            f"{BASE_URL}/v1/public/agent-versions/{ref_id}/problems"
            f"?phase=RACE&race_id={race['race_id']}",
            min_interval=min_interval,
        ).get("problems") or []
        agent_names = ", ".join(name for name, _, _ in agents)
        print(
            f"Race #{rn} (top {len(agents)}: {agent_names}): {len(problems)} RACE problems",
            flush=True,
        )
        for name, aid, _score in agents:
            probs = get_json(
                f"{BASE_URL}/v1/public/agent-versions/{aid}/problems"
                f"?phase=RACE&race_id={race['race_id']}",
                min_interval=min_interval,
            ).get("problems") or []
            prob_by_id = {p["problem_id"]: p for p in probs}
            for problem in problems:
                pid = problem["problem_id"]
                tasks.append(
                    {
                        "race_number": rn,
                        "race_id": race["race_id"],
                        "agent_name": name,
                        "agent_version_id": aid,
                        "problem": prob_by_id.get(pid, problem),
                    }
                )
    return tasks


def run_fetch_rounds(
    tasks: list[dict],
    checkpoint: dict[str, dict],
    checkpoint_path: Path,
    workers: int,
    min_interval: float,
    max_rounds: int,
) -> dict[str, dict]:
    pending = [
        t
        for t in tasks
        if task_key(
            t["race_number"],
            t["problem"]["problem_id"],
            t["agent_version_id"],
        )
        not in checkpoint
    ]
    for round_num in range(1, max_rounds + 1):
        if not pending:
            break
        print(
            f"Round {round_num}/{max_rounds}: fetching {len(pending)} problems "
            f"({len(checkpoint)} done)",
            flush=True,
        )
        failed: list[dict] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_task, task, min_interval): task for task in pending
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                task = futures[fut]
                key = task_key(
                    task["race_number"],
                    task["problem"]["problem_id"],
                    task["agent_version_id"],
                )
                try:
                    result = fut.result()
                    if result and result.get("query"):
                        with _checkpoint_lock:
                            existing = checkpoint.get(key)
                            if existing and (existing.get("correct_answer") or "").strip():
                                if not (result.get("correct_answer") or "").strip():
                                    continue
                            checkpoint[key] = result
                            if done % 25 == 0:
                                save_checkpoint(checkpoint_path, checkpoint)
                    else:
                        failed.append(task)
                except Exception as exc:
                    failed.append(task)
                    if done <= 3:
                        print(f"  warn {key}: {exc}", flush=True)
                if done % 50 == 0 or done == len(pending):
                    print(
                        f"  round progress {done}/{len(pending)} "
                        f"total_ok={len(checkpoint)}",
                        flush=True,
                    )
        save_checkpoint(checkpoint_path, checkpoint)
        pending = failed
        if failed:
            print(f"  round {round_num} failed: {len(failed)} — cooling down 15s", flush=True)
            time.sleep(15)
    return checkpoint


def build_csv_rows(checkpoint: dict[str, dict], from_race: int, to_race: int) -> list[dict]:
    filtered = [
        r
        for r in checkpoint.values()
        if r.get("query")
        and from_race <= int(r["race_number"]) <= to_race
        and r.get("category") in {"Product", "Shop", "Voucher"}
    ]
    # Prefer passed answers; among those keep the first seen per (race, query).
    best: dict[tuple[int, str], dict] = {}
    for item in sorted(
        filtered,
        key=lambda r: (
            int(r["race_number"]),
            0 if (r.get("correct_answer") or "").strip() else 1,
            r.get("category", ""),
            r["query"],
        ),
    ):
        key = (int(item["race_number"]), normalize_query(item["query"]))
        existing = best.get(key)
        if not existing:
            best[key] = item
            continue
        if not (existing.get("correct_answer") or "").strip() and (
            item.get("correct_answer") or ""
        ).strip():
            best[key] = item
    usable = sorted(best.values(), key=lambda r: (int(r["race_number"]), r.get("category", ""), r["query"]))
    rows: list[dict] = []
    for idx, item in enumerate(usable, start=1):
        query = normalize_query(item["query"])
        rows.append(
            {
                "id": str(idx),
                "race_number": str(item["race_number"]),
                "category": item["category"],
                "query": query,
                "query_code": encode_query(query),
                "frequency": "1",
                "appeared_race_numbers": "",
                "correct_answer": (item.get("correct_answer") or "").strip(),
                "answer_agent": (item.get("agent_name") or "").strip(),
            }
        )
    for query in {normalize_query(r["query"]) for r in rows}:
        sync_query_frequency(rows, query)
    sync_appeared_race_numbers(rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-race", type=int, default=1)
    parser.add_argument("--to-race", type=int, default=70)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ORO_RACE / "race-problems-queries-2026-06-22.csv",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "data"
        / "races"
        / "race_queries_checkpoint.json",
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--min-interval", type=float, default=0.08)
    parser.add_argument("--max-rounds", type=int, default=8)
    parser.add_argument(
        "--top-agents",
        type=int,
        default=15,
        help="Number of top-scoring agents to scan per race (default: 15)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    races = fetch_race_history(args.min_interval)
    targets = [
        r
        for r in races
        if args.from_race <= r.get("race_number", 0) <= args.to_race
        and r.get("status") == "RACE_COMPLETE"
        and r.get("winner_agent_version_id")
    ]
    targets.sort(key=lambda r: r["race_number"])

    tasks = build_tasks(
        targets,
        args.from_race,
        args.to_race,
        args.min_interval,
        top_agents=args.top_agents,
    )
    print(f"Queued {len(tasks)} RACE problems", flush=True)

    checkpoint = load_checkpoint(args.checkpoint)
    checkpoint = run_fetch_rounds(
        tasks,
        checkpoint,
        args.checkpoint,
        args.workers,
        args.min_interval,
        args.max_rounds,
    )

    csv_rows = build_csv_rows(checkpoint, args.from_race, args.to_race)
    passed_rows = sum(1 for r in csv_rows if (r.get("correct_answer") or "").strip())
    races_covered = len({r["race_number"] for r in csv_rows})
    print(
        f"Done: {len(csv_rows)} rows | races {races_covered} | "
        f"passed answers {passed_rows} | query-only {len(csv_rows)-passed_rows}",
        flush=True,
    )

    if args.dry_run:
        return 0

    if len(csv_rows) < len(tasks) * 0.9:
        print(
            f"WARNING: only {len(csv_rows)}/{len(tasks)} problems extracted. "
            f"Re-run to resume from checkpoint.",
            flush=True,
        )

    if args.csv.exists():
        existing_rows = list(
            __import__("csv").DictReader(args.csv.open(encoding="utf-8"))
        )
        answered_before = sum(
            1 for r in existing_rows if (r.get("correct_answer") or "").strip()
        )
        new_keys = {
            (r["race_number"], normalize_query(r["query"]))
            for r in csv_rows
            if r.get("query")
        }
        merged = [
            r
            for r in existing_rows
            if (r.get("race_number"), normalize_query(r.get("query") or "")) not in new_keys
        ]
        merged.extend(csv_rows)
        answered_after = sum(
            1 for r in merged if (r.get("correct_answer") or "").strip()
        )
        if answered_after < answered_before:
            raise SystemExit(
                f"Refusing to write: merge would drop answered rows "
                f"{answered_before} -> {answered_after}"
            )
        backup = args.csv.with_suffix(".csv.bak2")
        shutil.copy2(args.csv, backup)
        write_csv_rows(args.csv, merged)
        print(
            f"Merged {len(csv_rows)} rows into CSV "
            f"(total {len(merged)}, answered {answered_after})"
        )
    else:
        write_csv_rows(args.csv, csv_rows)
        print(f"Created {args.csv} with {len(csv_rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
