#!/usr/bin/env python3
"""Monitor an in-progress race and fetch passed correct answers from all agents.

Polls the public API, extracts answers from eval logs where validator status is
SUCCESS, and merges them into the race-problems-queries CSV.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORO_RACE = Path(__file__).resolve().parents[1] / "oro_race"
sys.path.insert(0, str(ORO_RACE))

from view_race_queries import (  # noqa: E402
    normalize_query,
    read_csv_rows,
    sync_answers_in_rows,
    write_csv_rows,
)

BASE_URL = "https://api.oroagents.com"
DEFAULT_LOG = Path(__file__).resolve().parents[1] / "logs" / "monitor_race_answers.jsonl"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "data" / "races" / "race_problem_query_map.json"


def log_line(log_path: Path | None, event: str, **fields) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def get_json(url: str, timeout: int = 60, min_interval: float = 0.08) -> dict | list:
    time.sleep(min_interval)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 1.0
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 502, 503, 504) and attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
        except urllib.error.URLError:
            if attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise


def post_json(url: str, body: dict, timeout: int = 60, min_interval: float = 0.08) -> dict:
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 502, 503, 504) and attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
        except urllib.error.URLError:
            if attempt + 1 < 8:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise


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
    time.sleep(min_interval)
    with urllib.request.urlopen(meta["download_url"], timeout=120) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode())
    except Exception:
        return json.loads(gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode())


def resolve_race(race_number: int | None) -> tuple[dict, list[dict]]:
    if race_number is None:
        current = get_json(f"{BASE_URL}/v1/public/races/current")
        return current["race"], current.get("qualifiers") or []

    history = get_json(f"{BASE_URL}/v1/public/races/history?limit=100&offset=0")
    for item in history.get("races") or []:
        if int(item.get("race_number", 0)) == race_number:
            detail = get_json(f"{BASE_URL}/v1/public/races/{item['race_id']}")
            return detail.get("race") or item, detail.get("qualifiers") or []
    current = get_json(f"{BASE_URL}/v1/public/races/current")
    race = current["race"]
    if int(race.get("race_number", 0)) == race_number:
        return race, current.get("qualifiers") or []
    raise SystemExit(f"Race {race_number} not found")


def evaluated_agents(qualifiers: list[dict]) -> list[tuple[str, str, float]]:
    agents: list[tuple[str, str, float]] = []
    for q in qualifiers:
        score = q.get("race_score")
        if score is None:
            continue
        agents.append((q.get("agent_name") or "?", q["agent_version_id"], float(score)))
    agents.sort(key=lambda item: (-item[2], item[0]))
    return agents


def build_agent_maps(
    race_id: str,
    agents: list[tuple[str, str, float]],
    min_interval: float,
) -> tuple[list[str], dict[str, dict[str, dict]], dict[str, int]]:
    ref_id = agents[0][1]
    problems = get_json(
        f"{BASE_URL}/v1/public/agent-versions/{ref_id}/problems"
        f"?phase=RACE&race_id={race_id}",
        min_interval=min_interval,
    ).get("problems") or []
    problem_ids = [p["problem_id"] for p in problems]

    agent_maps: dict[str, dict[str, dict]] = {}
    success_counts: dict[str, int] = {}
    for name, aid, _score in agents:
        probs = get_json(
            f"{BASE_URL}/v1/public/agent-versions/{aid}/problems"
            f"?phase=RACE&race_id={race_id}",
            min_interval=min_interval,
        ).get("problems") or []
        agent_maps[aid] = {p["problem_id"]: p for p in probs}
        success_counts[name] = sum(
            1
            for p in probs
            if any(vr.get("status") == "SUCCESS" for vr in (p.get("validator_results") or []))
        )
    return problem_ids, agent_maps, success_counts


def iter_success_tasks(
    problem_ids: list[str],
    agents: list[tuple[str, str, float]],
    agent_maps: dict[str, dict[str, dict]],
    *,
    one_per_problem: bool,
) -> list[dict]:
    tasks: list[dict] = []
    for pid in problem_ids:
        picked = False
        for name, aid, score in agents:
            problem = agent_maps.get(aid, {}).get(pid)
            if not problem:
                continue
            for vr in problem.get("validator_results") or []:
                if vr.get("status") != "SUCCESS":
                    continue
                tasks.append(
                    {
                        "agent_name": name,
                        "agent_version_id": aid,
                        "agent_score": score,
                        "problem_id": pid,
                        "category": problem.get("category") or "",
                        "eval_run_id": vr["eval_run_id"],
                    }
                )
                if one_per_problem:
                    picked = True
                    break
            if picked:
                break
    return tasks


def load_query_map(cache_path: Path, race_number: int) -> dict[str, str]:
    if not cache_path.exists():
        return {}
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    return {k: v for k, v in (data.get(str(race_number)) or {}).items()}


def save_query_map(cache_path: Path, race_number: int, mapping: dict[str, str]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    data[str(race_number)] = mapping
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_query_map(
    race_number: int,
    problem_ids: list[str],
    agents: list[tuple[str, str, float]],
    agent_maps: dict[str, dict[str, dict]],
    cache_path: Path,
    min_interval: float,
    log_path: Path | None,
) -> dict[str, str]:
    mapping = load_query_map(cache_path, race_number)
    missing_pids = [pid for pid in problem_ids if pid not in mapping]
    if not missing_pids:
        return mapping

    for pid in missing_pids:
        for name, aid, _score in agents:
            problem = agent_maps.get(aid, {}).get(pid)
            if not problem:
                continue
            eval_run_id = next(
                (
                    vr["eval_run_id"]
                    for vr in (problem.get("validator_results") or [])
                    if vr.get("status") == "SUCCESS"
                ),
                None,
            )
            if not eval_run_id:
                eval_run_id = (problem.get("validator_results") or [{}])[0].get("eval_run_id")
            if not eval_run_id:
                continue
            try:
                steps = fetch_logs(aid, eval_run_id, pid, min_interval)
                query = extract_query(steps)
                if query:
                    mapping[pid] = query
                    break
            except Exception as exc:
                log_line(log_path, "query_map_error", problem_id=pid, agent=name, error=str(exc))
    save_query_map(cache_path, race_number, mapping)
    log_line(log_path, "query_map_ready", mapped=len(mapping), total=len(problem_ids))
    return mapping


def fetch_for_missing(
    problem_ids: list[str],
    agents: list[tuple[str, str, float]],
    agent_maps: dict[str, dict[str, dict]],
    query_map: dict[str, str],
    missing_queries: set[str],
    min_interval: float,
    log_path: Path | None,
) -> dict[str, dict]:
    answers: dict[str, dict] = {}
    errors = 0
    fetches = 0
    target_pids = [pid for pid in problem_ids if query_map.get(pid) in missing_queries]
    for pid in target_pids:
        query_target = query_map[pid]
        if query_target not in missing_queries:
            continue
        for name, aid, score in agents:
            problem = agent_maps.get(aid, {}).get(pid)
            if not problem:
                continue
            eval_run_id = next(
                (
                    vr["eval_run_id"]
                    for vr in (problem.get("validator_results") or [])
                    if vr.get("status") == "SUCCESS"
                ),
                None,
            )
            if not eval_run_id:
                continue
            fetches += 1
            try:
                steps = fetch_logs(aid, eval_run_id, pid, min_interval)
                query = extract_query(steps)
                answer = extract_final_product_ids(steps)
                if query != query_target or not answer:
                    continue
                answers[query] = {
                    "query": query,
                    "correct_answer": answer,
                    "category": problem.get("category") or "",
                    "agent_name": name,
                    "problem_id": pid,
                }
                missing_queries.discard(query)
                break
            except Exception as exc:
                errors += 1
                if errors <= 3:
                    log_line(
                        log_path,
                        "fetch_error",
                        problem_id=pid,
                        agent=name,
                        error=str(exc),
                    )
    log_line(
        log_path,
        "fetch_progress",
        fetches=fetches,
        unique_answers=len(answers),
        errors=errors,
        missing_left=len(missing_queries),
        target_problems=len(target_pids),
    )
    return answers


def missing_race_queries(csv_path: Path, race_number: int) -> set[str]:
    rows = read_csv_rows(csv_path)
    return {
        normalize_query(row["query"])
        for row in rows
        if row.get("race_number") == str(race_number)
        and row.get("query")
        and not (row.get("correct_answer") or "").strip()
    }


def missing_solver_queries(csv_path: Path, race_number: int) -> dict[str, str]:
    """Queries in a race that have an answer but no solver attribution yet."""
    rows = read_csv_rows(csv_path)
    missing: dict[str, str] = {}
    for row in rows:
        if row.get("race_number") != str(race_number):
            continue
        query = normalize_query(row.get("query") or "")
        answer = (row.get("correct_answer") or "").strip()
        if not query or not answer or (row.get("answer_agent") or "").strip():
            continue
        missing[query] = answer
    return missing


def normalize_product_ids(raw: str) -> tuple[str, ...]:
    return tuple(sorted(token.strip() for token in raw.split(",") if token.strip()))


def fetch_agents_for_answered(
    answered_queries: dict[str, str],
    problem_ids: list[str],
    agents: list[tuple[str, str, float]],
    agent_maps: dict[str, dict[str, dict]],
    query_map: dict[str, str],
    min_interval: float,
    log_path: Path | None,
) -> dict[str, dict]:
    """Find solver agent names for queries that already have correct_answer."""
    pid_by_query = {normalize_query(query): pid for pid, query in query_map.items()}
    answers: dict[str, dict] = {}
    fetches = 0
    errors = 0

    for query, want_answer in answered_queries.items():
        pid = pid_by_query.get(query)
        if not pid:
            continue
        want_ids = normalize_product_ids(want_answer)
        matched: dict | None = None

        for name, aid, _score in agents:
            problem = agent_maps.get(aid, {}).get(pid)
            if not problem:
                continue
            for vr in problem.get("validator_results") or []:
                if vr.get("status") != "SUCCESS":
                    continue
                fetches += 1
                try:
                    steps = fetch_logs(aid, vr["eval_run_id"], pid, min_interval)
                    extracted = extract_final_product_ids(steps)
                except Exception as exc:
                    errors += 1
                    if errors <= 3:
                        log_line(
                            log_path,
                            "fetch_error",
                            problem_id=pid,
                            agent=name,
                            error=str(exc),
                        )
                    continue
                if not extracted:
                    continue
                if normalize_product_ids(extracted) == want_ids:
                    matched = {
                        "query": query,
                        "correct_answer": want_answer,
                        "category": problem.get("category") or "",
                        "agent_name": name,
                        "problem_id": pid,
                    }
                    break
            if matched:
                break

        if not matched:
            for name, aid, _score in agents:
                problem = agent_maps.get(aid, {}).get(pid)
                if not problem:
                    continue
                if any(vr.get("status") == "SUCCESS" for vr in (problem.get("validator_results") or [])):
                    matched = {
                        "query": query,
                        "correct_answer": want_answer,
                        "category": problem.get("category") or "",
                        "agent_name": name,
                        "problem_id": pid,
                    }
                    break

        if matched:
            answers[query] = matched

    log_line(
        log_path,
        "fetch_solver_progress",
        target_queries=len(answered_queries),
        unique_answers=len(answers),
        fetches=fetches,
        errors=errors,
    )
    return answers


def fetch_passed_answers(
    tasks: list[dict],
    min_interval: float,
    log_path: Path | None,
    *,
    missing_queries: set[str] | None = None,
    all_agents: bool = False,
) -> dict[str, dict]:
    answers: dict[str, dict] = {}
    errors = 0
    for i, task in enumerate(tasks, 1):
        try:
            steps = fetch_logs(
                task["agent_version_id"],
                task["eval_run_id"],
                task["problem_id"],
                min_interval,
            )
            query = extract_query(steps)
            answer = extract_final_product_ids(steps)
            if not query or not answer:
                continue
            if missing_queries is not None and query not in missing_queries:
                if not all_agents:
                    continue
            existing = answers.get(query)
            if existing and existing["correct_answer"] == answer:
                continue
            answers[query] = {
                "query": query,
                "correct_answer": answer,
                "category": task["category"],
                "agent_name": task["agent_name"],
                "problem_id": task["problem_id"],
            }
            if missing_queries is not None and query in missing_queries:
                missing_queries.discard(query)
                if not all_agents and not missing_queries:
                    log_line(
                        log_path,
                        "fetch_progress",
                        tasks_done=i,
                        tasks_total=len(tasks),
                        unique_answers=len(answers),
                        errors=errors,
                        note="all_missing_filled",
                    )
                    break
        except Exception as exc:
            errors += 1
            if errors <= 3:
                log_line(
                    log_path,
                    "fetch_error",
                    problem_id=task["problem_id"],
                    agent=task["agent_name"],
                    error=str(exc),
                )
        if i % 10 == 0 or i == len(tasks):
            log_line(
                log_path,
                "fetch_progress",
                tasks_done=i,
                tasks_total=len(tasks),
                unique_answers=len(answers),
                errors=errors,
                missing_left=len(missing_queries) if missing_queries is not None else None,
            )
    return answers


def merge_into_csv(
    csv_path: Path,
    race_number: int,
    answers_by_query: dict[str, dict],
) -> tuple[int, int, int]:
    rows = read_csv_rows(csv_path)
    race_rows = [r for r in rows if r.get("race_number") == str(race_number)]
    existing_answered = {
        normalize_query(r["query"])
        for r in race_rows
        if (r.get("correct_answer") or "").strip()
    }

    updated = 0
    for row in rows:
        if row.get("race_number") != str(race_number):
            continue
        query = normalize_query(row.get("query") or "")
        hit = answers_by_query.get(query)
        if not hit:
            continue
        row_changed = False
        if (row.get("correct_answer") or "").strip() != hit["correct_answer"]:
            row["correct_answer"] = hit["correct_answer"]
            row_changed = True
        agent_name = (hit.get("agent_name") or "").strip()
        if agent_name and (row.get("answer_agent") or "").strip() != agent_name:
            row["answer_agent"] = agent_name
            row_changed = True
        if row_changed:
            updated += 1

    sync_answers_in_rows(rows)
    answered = sum(
        1
        for r in rows
        if r.get("race_number") == str(race_number) and (r.get("correct_answer") or "").strip()
    )
    if updated:
        backup = csv_path.with_suffix(".csv.bak2")
        if csv_path.exists():
            shutil.copy2(csv_path, backup)
        write_csv_rows(csv_path, rows)
    return updated, answered, len(race_rows)


def run_once(
    race_number: int | None,
    csv_path: Path,
    min_interval: float,
    log_path: Path | None,
    cache_path: Path = DEFAULT_CACHE,
) -> dict:
    race, qualifiers = resolve_race(race_number)
    rn = int(race["race_number"])
    race_id = race["race_id"]
    status = race.get("status")
    agents = evaluated_agents(qualifiers)
    if not agents:
        summary = {
            "race_number": rn,
            "race_id": race_id,
            "status": status,
            "agents_evaluated": 0,
            "success_counts": {},
            "csv_updated_rows": 0,
            "csv_answered": 0,
            "csv_total": 0,
            "csv_missing": 0,
        }
        log_line(log_path, "poll_complete", **summary)
        return summary

    problem_ids, agent_maps, success_counts = build_agent_maps(race_id, agents, min_interval)
    query_map = ensure_query_map(
        rn, problem_ids, agents, agent_maps, cache_path, min_interval, log_path
    )
    missing_left = missing_race_queries(csv_path, rn)
    missing_solver = missing_solver_queries(csv_path, rn)

    answers: dict[str, dict] = {}
    if missing_left:
        answers.update(
            fetch_for_missing(
                problem_ids,
                agents,
                agent_maps,
                query_map,
                set(missing_left),
                min_interval,
                log_path,
            )
        )

    if missing_solver:
        answers.update(
            fetch_agents_for_answered(
                missing_solver,
                problem_ids,
                agents,
                agent_maps,
                query_map,
                min_interval,
                log_path,
            )
        )
    elif not missing_left:
        tasks = iter_success_tasks(problem_ids, agents, agent_maps, one_per_problem=True)
        answers.update(fetch_passed_answers(tasks, min_interval, log_path))

    updated, answered, total = merge_into_csv(csv_path, rn, answers)
    missing_after = total - answered

    summary = {
        "race_number": rn,
        "race_id": race_id,
        "status": status,
        "agents_evaluated": len(agents),
        "success_counts": success_counts,
        "unique_passed_answers": len(answers),
        "csv_updated_rows": updated,
        "csv_answered": answered,
        "csv_total": total,
        "csv_missing": missing_after,
    }
    log_line(log_path, "poll_complete", **summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor race and fetch passed answers")
    parser.add_argument("--race", type=int, default=None, help="Race number (default: current)")
    parser.add_argument(
        "--csv",
        type=Path,
        default=ORO_RACE / "race-problems-queries-2026-06-22.csv",
    )
    parser.add_argument("--poll-seconds", type=float, default=120.0)
    parser.add_argument("--min-interval", type=float, default=0.08)
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()

    log_line(args.log, "monitor_start", race=args.race, poll_seconds=args.poll_seconds, once=args.once)

    try:
        while True:
            try:
                summary = run_once(args.race, args.csv, args.min_interval, args.log)
            except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                log_line(args.log, "poll_error", error=str(exc))
                if args.once:
                    return 1
                time.sleep(args.poll_seconds)
                continue
            if args.once:
                return 0
            if summary["status"] == "RACE_COMPLETE" and summary["csv_answered"] >= summary["csv_total"]:
                log_line(args.log, "monitor_done", reason="race_complete_all_answered", **summary)
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        log_line(args.log, "monitor_stop", reason="keyboard_interrupt")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
