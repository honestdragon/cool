#!/usr/bin/env python3
"""SN15 registration monitor dashboard.

Live dashboard for subnet 15 registrations: uid, hotkey, coldkey, registered time,
registration cost, and coldkey UID count. Group removal queue is on /to-be-removed.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from pathlib import Path
from threading import Timer

from flask import Flask, jsonify, render_template_string, request

from subnet_registration import (
    fetch_registration_monitor_rows,
    fetch_to_be_removed_rows,
    warm_block_time_cache,
)

ORO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATHS = [
    ORO_ROOT / ".env",
    ORO_ROOT / "reg_bot" / "config.env",
]
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

_seen_hotkeys: set[str] = set()
_seen_lock = threading.Lock()
_cache: dict | None = None
_cache_at: float = 0.0
_remove_cache: dict | None = None
_remove_cache_at: float = 0.0


def load_env() -> None:
    for path in ENV_PATHS:
        if not path.is_file():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _mark_new_rows(rows: list[dict]) -> tuple[list[dict], int]:
    global _seen_hotkeys
    new_count = 0
    with _seen_lock:
        first_snapshot = not _seen_hotkeys
        marked = []
        for row in rows:
            hotkey = row["hotkey"]
            is_new = hotkey not in _seen_hotkeys
            if is_new and not first_snapshot:
                new_count += 1
            marked.append({**row, "is_new": is_new and not first_snapshot})
            _seen_hotkeys.add(hotkey)
        return marked, new_count


def _attach_remove_ranks(rows: list[dict], to_be_removed: dict) -> None:
    rank_by_uid = {
        row["uid"]: row["remove_rank"]
        for row in to_be_removed.get("rows") or []
        if row.get("uid") is not None
    }
    for row in rows:
        row["remove_rank"] = rank_by_uid.get(row["uid"])


def _get_payload(*, force: bool = False, cache_seconds: float = 15.0) -> dict:
    global _cache, _cache_at
    now = time.monotonic()
    if not force and _cache is not None and (now - _cache_at) < cache_seconds:
        return _cache

    payload = fetch_registration_monitor_rows(max_block_lookups=50 if force else 20)
    to_be_removed = fetch_to_be_removed_rows()
    rows, new_count = _mark_new_rows(payload["rows"])
    _attach_remove_ranks(rows, to_be_removed)
    payload["rows"] = rows
    payload["to_be_removed"] = to_be_removed
    payload["new_since_last_fetch"] = new_count
    _cache = payload
    _cache_at = now
    return payload


def _get_remove_payload(*, force: bool = False, cache_seconds: float = 15.0) -> dict:
    global _remove_cache, _remove_cache_at
    now = time.monotonic()
    if not force and _remove_cache is not None and (now - _remove_cache_at) < cache_seconds:
        return _remove_cache

    payload = fetch_to_be_removed_rows()
    _remove_cache = payload
    _remove_cache_at = now
    return payload


COMMON_STYLES = """
    :root {
      color-scheme: dark;
      --bg: #0d0d0d;
      --panel: #141414;
      --panel-2: #1c1c1c;
      --border: #2e2e2e;
      --text: #e8e8e8;
      --muted: #9ca3af;
      --accent: #0891b2;
      --accent-hover: #0e7490;
      --header: #181818;
      --row-hover: #222222;
      --new-row: rgba(34, 197, 94, 0.15);
      --new-border: #22c55e;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", Calibri, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      padding: 14px 18px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 20;
    }
    .toolbar h1 {
      margin: 0;
      font-size: 1.2rem;
      font-weight: 600;
      margin-right: auto;
    }
    .toolbar label { font-size: 0.85rem; color: var(--muted); }
    .toolbar input, .toolbar select, .toolbar button, .toolbar a.nav-link {
      font: inherit;
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      text-decoration: none;
    }
    .toolbar button {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
      cursor: pointer;
    }
    .toolbar button:hover { background: var(--accent-hover); }
    .toolbar button.secondary, .toolbar a.nav-link {
      background: var(--panel-2);
      color: var(--text);
      border-color: var(--border);
    }
    .toolbar a.nav-link:hover { background: rgba(255, 255, 255, 0.06); }
    .toolbar a.nav-link.pulse-danger {
      color: #fca5a5;
      border-color: #ef4444;
      background: rgba(239, 68, 68, 0.12);
      animation: pulse-danger 2s ease-in-out infinite;
    }
    .toolbar a.nav-link.pulse-danger.imminent {
      color: #fff;
      background: rgba(239, 68, 68, 0.35);
      border-color: #f87171;
      animation: pulse-danger-imminent 1.2s ease-in-out infinite;
    }
    @keyframes pulse-danger {
      0%, 100% {
        box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.45);
        opacity: 1;
      }
      50% {
        box-shadow: 0 0 0 6px rgba(239, 68, 68, 0);
        opacity: 0.88;
      }
    }
    @keyframes pulse-danger-imminent {
      0%, 100% {
        box-shadow: 0 0 0 0 rgba(248, 113, 113, 0.7);
        transform: scale(1);
      }
      50% {
        box-shadow: 0 0 0 8px rgba(248, 113, 113, 0);
        transform: scale(1.03);
      }
    }
    .stats .danger-stat {
      color: #fca5a5;
    }
    .stats .danger-stat strong { color: #f87171; }
    .stats .danger-stat.hidden { display: none; }
    .stats {
      display: flex;
      flex-wrap: wrap;
      gap: 18px;
      padding: 12px 18px;
      background: var(--panel-2);
      border-bottom: 1px solid var(--border);
      font-size: 0.88rem;
      color: var(--muted);
    }
    .stats strong { color: var(--text); }
    .stats .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(34, 197, 94, 0.2);
      color: #4ade80;
      font-weight: 600;
      margin-left: 6px;
    }
    .stats .badge.hidden { display: none; }
    .stats .team-daily-stat {
      padding: 4px 12px;
      border-radius: 8px;
      background: rgba(251, 191, 36, 0.12);
      border: 1px solid rgba(251, 191, 36, 0.35);
      color: #fcd34d;
    }
    .stats .team-daily-stat strong {
      color: #fbbf24;
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: 0.02em;
    }
    .stats .team-reg-stat {
      padding: 4px 12px;
      border-radius: 8px;
      background: rgba(8, 145, 178, 0.12);
      border: 1px solid rgba(8, 145, 178, 0.35);
      color: #67e8f9;
    }
    .stats .team-reg-stat strong {
      color: #22d3ee;
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: 0.02em;
    }
    .note, .panel-note {
      padding: 8px 18px;
      font-size: 0.82rem;
      color: var(--muted);
      border-bottom: 1px solid var(--border);
      background: #111;
    }
    #table-wrap, #remove-table-wrap, #reg-spend-table-wrap, #member-daily-table-wrap {
      margin: 12px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      min-height: 400px;
    }
    .tabulator, .tabulator .tabulator-tableholder {
      background: var(--panel) !important;
      color: var(--text);
      border: none;
    }
    .tabulator .tabulator-header {
      background: var(--header) !important;
      border-bottom: 1px solid var(--border);
    }
    .tabulator .tabulator-header .tabulator-col {
      background: var(--header) !important;
      color: var(--muted);
      border-right: 1px solid var(--border);
    }
    .tabulator-row {
      background: var(--panel) !important;
      border-bottom: 1px solid var(--border);
    }
    .tabulator-row:nth-child(even) { background: #121212 !important; }
    .tabulator-row:hover { background: var(--row-hover) !important; }
    .tabulator-row.row-new {
      background: var(--new-row) !important;
      box-shadow: inset 3px 0 0 var(--new-border);
    }
    .tabulator-row.row-team {
      box-shadow: inset 3px 0 0 rgba(180, 155, 120, 0.55);
    }
    .tabulator-row.row-team:hover {
      background: var(--row-hover) !important;
    }
    .tabulator-row.row-validator {
      opacity: 0.55;
    }
    .tabulator-row.row-validator:hover {
      opacity: 0.75;
    }
    .today-date-badge {
      display: inline-block;
      margin-left: 8px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.68rem;
      font-weight: 500;
      letter-spacing: 0.04em;
      color: #94a3b8;
      background: rgba(148, 163, 184, 0.08);
      border: 1px solid rgba(148, 163, 184, 0.16);
      vertical-align: middle;
    }
    .tabulator-row.row-today {
      box-shadow: inset 2px 0 0 rgba(148, 163, 184, 0.28);
    }
    .tabulator-row.row-today:hover {
      background: var(--row-hover) !important;
    }
    .tabulator-row.row-date-divider:not(:first-child) {
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
    }
    .num-muted { color: #5a5a5a; font-weight: 400; }
    .daily-tao-cell { color: #c9a227; font-weight: 600; }
    .reg-fee-cell { color: #4da8b8; font-weight: 600; }
    .alive-cell { color: #5cb870; font-weight: 600; }
    .total-tao-cell { color: #c9a227; font-weight: 600; }
    .team-tao-cell { color: #4da8b8; font-weight: 600; }
    .validator-tag {
      display: inline-block;
      margin-left: 6px;
      padding: 1px 6px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 600;
      color: #c4b5fd;
      background: rgba(196, 181, 253, 0.12);
      border: 1px solid rgba(196, 181, 253, 0.25);
    }
    .team-name {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-weight: 500;
      font-size: 12px;
      color: #ddd6c8;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(180, 155, 120, 0.28);
    }
    .view-toggle {
      display: inline-flex;
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: var(--panel-2);
    }
    .view-toggle button {
      border: none;
      border-radius: 0;
      background: transparent;
      color: var(--muted);
      padding: 7px 12px;
    }
    .toolbar .view-toggle button {
      background: transparent;
      color: var(--muted);
      border: none;
    }
    .toolbar .view-toggle button.active {
      background: rgba(255, 255, 255, 0.08);
      color: var(--text);
    }
    .toolbar .view-toggle button:hover {
      background: rgba(255, 255, 255, 0.05);
    }
    .uid-list {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 11px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 280px;
      display: inline-block;
    }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
    .status-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #22c55e;
      margin-right: 6px;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .error-banner {
      display: none;
      padding: 10px 18px;
      background: rgba(248, 113, 113, 0.15);
      color: #fca5a5;
      border-bottom: 1px solid #7f1d1d;
    }
    .error-banner.visible { display: block; }
"""

LOCAL_TIME_JS = """
    const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "local";

    function parseRegisteredAt(value) {
      if (!value) return null;
      const raw = String(value).trim();
      let iso = raw;
      if (raw.endsWith(" UTC")) {
        iso = raw.slice(0, -4).trim().replace(" ", "T") + "Z";
      } else if (raw.includes(" ") && !raw.includes("T")) {
        iso = raw.replace(" ", "T") + "Z";
      }
      const dt = new Date(iso);
      return Number.isNaN(dt.getTime()) ? null : dt;
    }

    function localDateKey(dt) {
      const y = dt.getFullYear();
      const m = String(dt.getMonth() + 1).padStart(2, "0");
      const d = String(dt.getDate()).padStart(2, "0");
      return `${y}-${m}-${d}`;
    }

    function formatLocalDateTime(value) {
      const dt = parseRegisteredAt(value);
      if (!dt) return value ? String(value) : "-";
      return dt.toLocaleString(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }

    function formatFetchedAt(iso) {
      if (!iso) return "-";
      const dt = new Date(iso);
      if (Number.isNaN(dt.getTime())) return String(iso).slice(0, 19).replace("T", " ");
      return dt.toLocaleString(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }

    function compareRegisteredAt(a, b) {
      const da = parseRegisteredAt(a);
      const db = parseRegisteredAt(b);
      if (!da && !db) return 0;
      if (!da) return 1;
      if (!db) return -1;
      return da.getTime() - db.getTime();
    }

    function formatDateCell(value, todayDate) {
      if (!value) return "-";
      if (value === todayDate) {
        return `${value}<span class="today-date-badge">today</span>`;
      }
      return value;
    }

    function fmtMetricCell(value, activeClass, fmtFn) {
      const n = Number(value);
      const text = fmtFn ? fmtFn(value) : String(value ?? "-");
      if (!n) return `<span class="num-muted">${text}</span>`;
      return `<span class="${activeClass}">${text}</span>`;
    }

    function buildDailyRegistrationSpend(rows) {
      const byDay = new Map();
      let skippedMissingCost = 0;
      let skippedMissingDate = 0;
      for (const row of rows || []) {
        const cost = row.registration_cost_tao;
        if (cost == null || cost === "") {
          skippedMissingCost += 1;
          continue;
        }
        const dt = parseRegisteredAt(row.registered_at);
        if (!dt) {
          skippedMissingDate += 1;
          continue;
        }
        const day = localDateKey(dt);
        if (!byDay.has(day)) {
          byDay.set(day, {
            date: day,
            registrations: 0,
            total_tao: 0,
            team_tao: 0,
            team_registrations: 0,
          });
        }
        const bucket = byDay.get(day);
        bucket.registrations += 1;
        bucket.total_tao += Number(cost);
        if (row.is_team) {
          bucket.team_tao += Number(cost);
          bucket.team_registrations += 1;
        }
      }
      const recentDays = Array.from(byDay.values())
        .map((item) => ({
          ...item,
          total_tao: Math.round(item.total_tao * 1e6) / 1e6,
          team_tao: Math.round(item.team_tao * 1e6) / 1e6,
        }))
        .sort((a, b) => b.date.localeCompare(a.date));
      const today = localDateKey(new Date());
      const todayBucket = byDay.get(today);
      const todayStats = {
        date: today,
        registrations: todayBucket?.registrations || 0,
        total_tao: todayBucket ? Math.round(todayBucket.total_tao * 1e6) / 1e6 : 0,
        team_tao: todayBucket ? Math.round(todayBucket.team_tao * 1e6) / 1e6 : 0,
        team_registrations: todayBucket?.team_registrations || 0,
      };
      return {
        timezone: LOCAL_TZ,
        today: todayStats,
        recent_days: recentDays,
        skipped_missing_cost: skippedMissingCost,
        skipped_missing_date: skippedMissingDate,
      };
    }

    function buildRollingRegistrationSpend(rows, hours = 24) {
      const cutoff = Date.now() - hours * 60 * 60 * 1000;
      let total_tao = 0;
      let registrations = 0;
      let team_tao = 0;
      let team_registrations = 0;
      let skippedMissingCost = 0;
      let skippedMissingDate = 0;

      for (const row of rows || []) {
        const cost = row.registration_cost_tao;
        if (cost == null || cost === "") {
          skippedMissingCost += 1;
          continue;
        }
        const dt = parseRegisteredAt(row.registered_at);
        if (!dt) {
          skippedMissingDate += 1;
          continue;
        }
        if (dt.getTime() < cutoff) continue;
        registrations += 1;
        total_tao += Number(cost);
        if (row.is_team) {
          team_registrations += 1;
          team_tao += Number(cost);
        }
      }

      return {
        hours,
        registrations,
        total_tao: Math.round(total_tao * 1e6) / 1e6,
        team_tao: Math.round(team_tao * 1e6) / 1e6,
        team_registrations,
        skipped_missing_cost: skippedMissingCost,
        skipped_missing_date: skippedMissingDate,
      };
    }
"""

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SN15 Register Monitor</title>
  <link href="/static/tabulator_midnight.min.css?v=1" rel="stylesheet">
  <script src="/static/tabulator.min.js?v=1"></script>
  <style>
""" + COMMON_STYLES + """
  </style>
</head>
<body>
  <div class="toolbar">
    <h1><span class="status-dot" id="status-dot"></span>SN15 Register Monitor</h1>
    <a class="nav-link" id="nav-to-be-removed" href="/to-be-removed">To be removed</a>
    <a class="nav-link" href="/registration-spend">Reg spend</a>
    <a class="nav-link" href="/member-daily">Member daily</a>
    <label>
      Search
      <input type="search" id="search" placeholder="name, uid, hotkey, coldkey…" style="width: 260px;">
    </label>
    <label>
      <input type="checkbox" id="team-only"> Group only
    </label>
    <div class="view-toggle" role="group" aria-label="View mode">
      <button type="button" class="active" id="view-uid" data-view="uid">By UID</button>
      <button type="button" id="view-coldkey" data-view="coldkey">By coldkey</button>
    </div>
    <label>
      Auto refresh
      <select id="refresh-interval">
        <option value="15">15s</option>
        <option value="30" selected>30s</option>
        <option value="60">60s</option>
        <option value="120">2m</option>
        <option value="0">Off</option>
      </select>
    </label>
    <button type="button" id="refresh-btn">Refresh now</button>
    <button type="button" class="secondary" id="reset-new-btn" title="Clear new-row highlights">Reset new</button>
  </div>

  <div id="error-banner" class="error-banner"></div>

  <div class="stats">
    <span>Registered: <strong id="stat-registered">-</strong></span>
    <span>Coldkeys: <strong id="stat-coldkeys">-</strong></span>
    <span>Multi-UID coldkeys: <strong id="stat-multi">-</strong></span>
    <span>Max UIDs/coldkey: <strong id="stat-max-uid">-</strong></span>
    <span>Subnet daily TAO: <strong id="stat-daily-tao">-</strong> <span class="validator-tag">miners only</span></span>
    <span class="team-daily-stat">Group daily TAO: <strong id="stat-team-daily-tao">-</strong></span>
    <span title="All registrations in the last 24 hours">24h reg spend: <strong id="stat-today-reg-spend">-</strong></span>
    <span class="team-reg-stat" title="Group registrations in the last 24 hours">Group 24h reg: <strong id="stat-team-today-reg">-</strong></span>
    <span>Current reg cost: <strong id="stat-current-cost">-</strong> τ</span>
    <span>Reg costs loaded: <strong id="stat-cost-loaded">-</strong></span>
    <span>Reg times loaded: <strong id="stat-time-loaded">-</strong></span>
    <span>Group: <strong id="stat-team">-</strong></span>
    <span>Group at risk: <strong id="stat-team-risk">-</strong></span>
    <span>Best group rank: <strong id="stat-team-rank">-</strong></span>
    <span>Next 3 group dereg: <strong id="stat-next-team-dereg">-</strong></span>
    <span class="danger-stat hidden" id="stat-danger-wrap">
      At-risk member:
      <strong id="stat-danger-member">-</strong>
    </span>
    <span>New this refresh: <strong id="stat-new">0</strong><span class="badge hidden" id="new-badge">NEW</span></span>
    <span>Updated: <strong id="stat-fetched">-</strong></span>
  </div>

  <div class="note" id="cost-note">Loading…</div>

  <div id="table-wrap"></div>

  <script>
""" + LOCAL_TIME_JS + """
    let table = null;
    let refreshTimer = null;
    let knownNewHotkeys = new Set();
    let teamOnly = false;
    let viewMode = "uid";
    let lastRows = [];

    function fmtTao(value) {
      if (value == null || value === "") return "-";
      return Number(value).toFixed(4);
    }

    function formatTeamDeregList(teamRows, limit = 3) {
      const top = (teamRows || []).slice(0, limit);
      if (!top.length) return "-";
      return top.map((row) =>
        `${row.uid} (${row.team_name || "group"} #${row.remove_rank})`
      ).join(" · ");
    }

    function shortKey(value) {
      if (!value) return "-";
      if (value.length <= 16) return value;
      return value.slice(0, 8) + "…" + value.slice(-6);
    }

    function teamNameFormatter(cell) {
      const value = cell.getValue();
      return value ? `<span class="team-name">${value}</span>` : "-";
    }

    function formatRowElement(row) {
      const el = row.getElement();
      const data = row.getData();
      el.classList.toggle("row-new", viewMode === "uid" && knownNewHotkeys.has(data.hotkey));
      el.classList.toggle("row-team", !!data.is_team);
      el.classList.toggle("row-validator", viewMode === "uid" && !!data.is_validator);
    }

    function buildColdkeyGroups(rows) {
      const groups = new Map();
      for (const row of rows) {
        const coldkey = row.coldkey || "";
        if (!groups.has(coldkey)) {
          groups.set(coldkey, {
            coldkey,
            team_name: row.team_name || null,
            is_team: !!row.is_team,
            coldkey_uid_count: row.coldkey_uid_count || 0,
            coldkey_total_reg_cost_tao: row.coldkey_total_reg_cost_tao,
            coldkey_total_emission: row.coldkey_total_emission,
            coldkey_daily_tao: row.coldkey_daily_tao,
            uids: [],
            latest_registered_at: null,
            earliest_registered_at: null,
            latest_registered_ts: null,
            earliest_registered_ts: null,
          });
        }
        const group = groups.get(coldkey);
        group.uids.push(row.uid);
        const registeredAt = parseRegisteredAt(row.registered_at);
        if (registeredAt) {
          const ts = registeredAt.getTime();
          if (group.latest_registered_ts == null || ts > group.latest_registered_ts) {
            group.latest_registered_ts = ts;
            group.latest_registered_at = row.registered_at;
          }
          if (group.earliest_registered_ts == null || ts < group.earliest_registered_ts) {
            group.earliest_registered_ts = ts;
            group.earliest_registered_at = row.registered_at;
          }
        }
      }
      return Array.from(groups.values())
        .map((group) => {
          group.uids.sort((a, b) => a - b);
          group.uid_list = group.uids.join(", ");
          group.earliest_registered_at = formatLocalDateTime(group.earliest_registered_at);
          group.latest_registered_at = formatLocalDateTime(group.latest_registered_at);
          delete group.latest_registered_ts;
          delete group.earliest_registered_ts;
          return group;
        })
        .sort((a, b) => {
          if (a.is_team !== b.is_team) return a.is_team ? -1 : 1;
          return (b.coldkey_daily_tao || 0) - (a.coldkey_daily_tao || 0)
            || (b.coldkey_total_emission || 0) - (a.coldkey_total_emission || 0);
        });
    }

    const uidColumns = [
      { title: "UID", field: "uid", width: 70, sorter: "number", hozAlign: "right",
        formatter: (cell) => {
          const data = cell.getRow().getData();
          const uid = cell.getValue();
          return data.is_validator ? `${uid}<span class="validator-tag">V</span>` : String(uid);
        },
      },
      {
        title: "Remove #",
        field: "remove_rank",
        width: 85,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => {
          const value = cell.getValue();
          return value == null || value === "" ? "-" : String(value);
        },
      },
      { title: "Name", field: "team_name", width: 100, formatter: teamNameFormatter },
      {
        title: "Hotkey",
        field: "hotkey",
        minWidth: 150,
        formatter: (cell) => `<span class="mono" title="${cell.getValue()}">${shortKey(cell.getValue())}</span>`,
      },
      {
        title: "Coldkey",
        field: "coldkey",
        minWidth: 150,
        formatter: (cell) => `<span class="mono" title="${cell.getValue()}">${shortKey(cell.getValue())}</span>`,
      },
      {
        title: "Registered",
        field: "registered_at",
        minWidth: 170,
        sorter: (a, b) => compareRegisteredAt(a, b),
        formatter: (cell) => formatLocalDateTime(cell.getValue()),
      },
      {
        title: "Reg cost (τ)",
        field: "registration_cost_tao",
        width: 110,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtTao(cell.getValue()),
      },
      {
        title: "Coldkey UIDs",
        field: "coldkey_uid_count",
        width: 110,
        hozAlign: "right",
        sorter: "number",
      },
      {
        title: "Coldkey total (τ)",
        field: "coldkey_total_reg_cost_tao",
        width: 120,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtTao(cell.getValue()),
      },
    ];

    const coldkeyColumns = [
      { title: "Name", field: "team_name", width: 110, formatter: teamNameFormatter },
      {
        title: "Coldkey",
        field: "coldkey",
        minWidth: 160,
        formatter: (cell) => `<span class="mono" title="${cell.getValue()}">${shortKey(cell.getValue())}</span>`,
      },
      {
        title: "UIDs",
        field: "coldkey_uid_count",
        width: 70,
        hozAlign: "right",
        sorter: "number",
      },
      {
        title: "Total reg cost (τ)",
        field: "coldkey_total_reg_cost_tao",
        width: 130,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtTao(cell.getValue()),
      },
      {
        title: "Total emission",
        field: "coldkey_total_emission",
        width: 120,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtTao(cell.getValue()),
      },
      {
        title: "Daily TAO (τ)",
        field: "coldkey_daily_tao",
        width: 120,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtTao(cell.getValue()),
      },
      {
        title: "First registered",
        field: "earliest_registered_at",
        minWidth: 170,
        sorter: (a, b) => compareRegisteredAt(a, b),
      },
      {
        title: "Latest registered",
        field: "latest_registered_at",
        minWidth: 170,
        sorter: (a, b) => compareRegisteredAt(a, b),
      },
      {
        title: "UID list",
        field: "uid_list",
        minWidth: 220,
        formatter: (cell) => `<span class="uid-list" title="${cell.getValue() || ""}">${cell.getValue() || "-"}</span>`,
      },
    ];

    function destroyMainTable() {
      if (table) {
        table.destroy();
        table = null;
      }
    }

    function renderTable(rows) {
      lastRows = rows || [];
      destroyMainTable();
      const data = viewMode === "coldkey" ? buildColdkeyGroups(lastRows) : lastRows;
      const columns = viewMode === "coldkey" ? coldkeyColumns : uidColumns;
      const initialSort = viewMode === "coldkey"
        ? [{ column: "coldkey_daily_tao", dir: "desc" }]
        : [{ column: "registered_at", dir: "desc", sorter: (a, b) => compareRegisteredAt(a, b) }];

      table = new Tabulator("#table-wrap", {
        data,
        columns,
        layout: "fitDataStretch",
        height: "calc(100vh - 210px)",
        placeholder: "No registrations loaded",
        initialSort,
        rowFormatter: formatRowElement,
      });
      applySearch();
    }

    function applySearch() {
      const q = document.getElementById("search").value.trim().toLowerCase();
      if (!table) return;
      if (!q && !teamOnly) {
        table.clearFilter(true);
        return;
      }
      table.setFilter((data) => {
        if (teamOnly && !data.is_team) return false;
        if (!q) return true;
        if (viewMode === "coldkey") {
          return (data.team_name || "").toLowerCase().includes(q)
            || (data.coldkey || "").toLowerCase().includes(q)
            || String(data.coldkey_uid_count || "").includes(q)
            || (data.uid_list || "").includes(q);
        }
        return String(data.uid).includes(q)
          || (data.team_name || "").toLowerCase().includes(q)
          || (data.hotkey || "").toLowerCase().includes(q)
          || (data.coldkey || "").toLowerCase().includes(q)
          || String(data.remove_rank ?? "").includes(q);
      });
    }

    function setViewMode(mode) {
      viewMode = mode;
      document.getElementById("view-uid").classList.toggle("active", mode === "uid");
      document.getElementById("view-coldkey").classList.toggle("active", mode === "coldkey");
      renderTable(lastRows);
    }

    function updateSummary(payload) {
      const s = payload.summary || {};
      const remove = payload.to_be_removed || {};
      const removeSummary = remove.summary || {};
      const rolling24 = buildRollingRegistrationSpend(payload.rows || [], 24);
      document.getElementById("stat-registered").textContent =
        `${s.registered_count || 0}/${s.total_slots || 0}`;
      document.getElementById("stat-coldkeys").textContent = s.unique_coldkeys ?? "-";
      document.getElementById("stat-multi").textContent = s.multi_uid_coldkeys ?? "-";
      document.getElementById("stat-max-uid").textContent = s.max_coldkey_uid_count ?? "-";
      document.getElementById("stat-daily-tao").textContent =
        s.total_daily_tao != null ? `${fmtTao(s.total_daily_tao)}/day` : "-";
      document.getElementById("stat-team-daily-tao").textContent =
        s.team_daily_tao != null
          ? `${fmtTao(s.team_daily_tao)}/day (${s.team_daily_tao_pct || 0}%)`
          : "-";
      document.getElementById("stat-today-reg-spend").textContent =
        `${fmtTao(rolling24.total_tao)} τ (${rolling24.registrations} UID${rolling24.registrations === 1 ? "" : "s"})`;
      document.getElementById("stat-team-today-reg").textContent =
        `${fmtTao(rolling24.team_tao)} τ (${rolling24.team_registrations} UID${rolling24.team_registrations === 1 ? "" : "s"})`;
      document.getElementById("stat-current-cost").textContent =
        s.current_registration_cost_tao != null ? fmtTao(s.current_registration_cost_tao) : "-";
      document.getElementById("stat-cost-loaded").textContent =
        `${s.known_registration_costs || 0}/${s.registered_count || 0}`;
      document.getElementById("stat-time-loaded").textContent =
        `${s.known_registration_times || 0}/${s.registered_count || 0}`;
      document.getElementById("stat-team").textContent =
        `${s.team_uids || 0} UIDs / ${s.team_registered_members || 0}/${s.team_members || 0} members`;

      document.getElementById("stat-team-risk").textContent = removeSummary.team_at_risk ?? 0;
      document.getElementById("stat-team-rank").textContent =
        removeSummary.best_team_remove_rank != null ? `#${removeSummary.best_team_remove_rank}` : "-";
      const teamRows = remove.team_rows || [];
      const nextDereg = remove.next_to_remove;
      const nextTeam = remove.next_team_at_risk;
      document.getElementById("stat-next-team-dereg").textContent =
        formatTeamDeregList(teamRows, 3);

      const navRemove = document.getElementById("nav-to-be-removed");
      const teamAtRisk = (removeSummary.team_at_risk || 0) > 0;
      const imminentTeamDereg = nextDereg && nextDereg.is_team;
      navRemove.classList.toggle("pulse-danger", teamAtRisk);
      navRemove.classList.toggle("imminent", imminentTeamDereg);

      const dangerWrap = document.getElementById("stat-danger-wrap");
      const dangerMember = document.getElementById("stat-danger-member");
      const dangerRow = imminentTeamDereg ? nextDereg : nextTeam;
      const showDanger = teamAtRisk && dangerRow;
      dangerWrap.classList.toggle("hidden", !showDanger);
      if (showDanger) {
        const regCost = dangerRow.registration_cost_tao != null
          ? fmtTao(dangerRow.registration_cost_tao)
          : "-";
        const parts = [
          `UID ${dangerRow.uid}`,
          dangerRow.team_name || "group",
          `#${dangerRow.remove_rank}`,
          `reg ${regCost} τ`,
        ];
        if (imminentTeamDereg) {
          parts.push("NEXT DEREG");
        }
        dangerMember.textContent = parts.join(" · ");
      } else {
        dangerMember.textContent = "-";
      }

      const newCount = payload.new_since_last_fetch || 0;
      document.getElementById("stat-new").textContent = newCount;
      document.getElementById("new-badge").classList.toggle("hidden", newCount === 0);

      const fetched = payload.fetched_at || "";
      document.getElementById("stat-fetched").textContent = formatFetchedAt(fetched);

      const costs = payload.registration_costs || {};
      const times = payload.registration_times || {};
      const emission = payload.emission || {};
      const notes = [times.note, costs.note].filter(Boolean);
      if (emission.miners_only) {
        const validators = payload.summary?.validator_count ?? emission.validator_count ?? 0;
        notes.push(`Emission/daily TAO excludes ${validators} validator UID(s).`);
      }
      document.getElementById("cost-note").textContent = notes.join(" | ");
    }

    async function fetchData(force = false) {
      const btn = document.getElementById("refresh-btn");
      const banner = document.getElementById("error-banner");
      btn.disabled = true;
      btn.textContent = "Refreshing…";
      try {
        const url = force ? "/api/registrations?force=1" : "/api/registrations";
        const resp = await fetch(url);
        const payload = await resp.json();
        if (!resp.ok) throw new Error(payload.error || resp.statusText);

        for (const row of payload.rows || []) {
          if (row.is_new) knownNewHotkeys.add(row.hotkey);
        }

        renderTable(payload.rows || []);
        updateSummary(payload);
        banner.classList.remove("visible");
        banner.textContent = "";
      } catch (err) {
        banner.textContent = `Fetch failed: ${err.message}`;
        banner.classList.add("visible");
      } finally {
        btn.disabled = false;
        btn.textContent = "Refresh now";
      }
    }

    function scheduleRefresh() {
      if (refreshTimer) clearInterval(refreshTimer);
      const seconds = Number(document.getElementById("refresh-interval").value);
      if (seconds > 0) {
        refreshTimer = setInterval(() => fetchData(false), seconds * 1000);
      }
    }

    document.getElementById("refresh-btn").addEventListener("click", () => fetchData(true));
    document.getElementById("search").addEventListener("input", applySearch);
    document.getElementById("team-only").addEventListener("change", (event) => {
      teamOnly = event.target.checked;
      applySearch();
    });
    document.getElementById("view-uid").addEventListener("click", () => setViewMode("uid"));
    document.getElementById("view-coldkey").addEventListener("click", () => setViewMode("coldkey"));
    document.getElementById("refresh-interval").addEventListener("change", scheduleRefresh);
    document.getElementById("reset-new-btn").addEventListener("click", () => {
      knownNewHotkeys.clear();
      if (table) table.redraw(true);
    });

    fetchData(true);
    scheduleRefresh();
  </script>
</body>
</html>
"""


HTML_REMOVE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SN15 To be Removed — Group</title>
  <link href="/static/tabulator_midnight.min.css?v=1" rel="stylesheet">
  <script src="/static/tabulator.min.js?v=1"></script>
  <style>
""" + COMMON_STYLES + """
  </style>
</head>
<body>
  <div class="toolbar">
    <h1><span class="status-dot" id="status-dot"></span>To be removed — group UIDs</h1>
    <a class="nav-link" href="/">Registrations</a>
    <a class="nav-link" href="/registration-spend">Reg spend</a>
    <a class="nav-link" href="/member-daily">Member daily</a>
    <label>
      Search
      <input type="search" id="search" placeholder="name, uid, hotkey, coldkey…" style="width: 260px;">
    </label>
    <label>
      Auto refresh
      <select id="refresh-interval">
        <option value="15">15s</option>
        <option value="30" selected>30s</option>
        <option value="60">60s</option>
        <option value="120">2m</option>
        <option value="0">Off</option>
      </select>
    </label>
    <button type="button" id="refresh-btn">Refresh now</button>
  </div>

  <div id="error-banner" class="error-banner"></div>

  <div class="stats">
    <span>Group at risk: <strong id="stat-team-risk">-</strong></span>
    <span>Best group rank: <strong id="stat-team-rank">-</strong></span>
    <span>Next 3 group dereg: <strong id="stat-next-team-dereg">-</strong></span>
    <span>Eligible (subnet): <strong id="stat-eligible">-</strong></span>
    <span>Updated: <strong id="stat-fetched">-</strong></span>
  </div>

  <div class="panel-note" id="remove-note">Loading removal queue…</div>

  <div id="remove-table-wrap"></div>

  <script>
""" + LOCAL_TIME_JS + """
    let table = null;
    let refreshTimer = null;

    function fmtTao(value) {
      if (value == null || value === "") return "-";
      return Number(value).toFixed(4);
    }

    function formatTeamDeregList(teamRows, limit = 3) {
      const top = (teamRows || []).slice(0, limit);
      if (!top.length) return "-";
      return top.map((row) =>
        `${row.uid} (${row.team_name || "group"} #${row.remove_rank})`
      ).join(" · ");
    }

    function shortKey(value) {
      if (!value) return "-";
      if (value.length <= 16) return value;
      return value.slice(0, 8) + "…" + value.slice(-6);
    }

    function teamNameFormatter(cell) {
      const value = cell.getValue();
      return value ? `<span class="team-name">${value}</span>` : "-";
    }

    const columns = [
      { title: "#", field: "remove_rank", width: 55, hozAlign: "right", sorter: "number" },
      { title: "UID", field: "uid", width: 70, hozAlign: "right", sorter: "number" },
      { title: "Name", field: "team_name", width: 100, formatter: teamNameFormatter },
      {
        title: "Hotkey",
        field: "hotkey",
        minWidth: 150,
        formatter: (cell) => `<span class="mono" title="${cell.getValue()}">${shortKey(cell.getValue())}</span>`,
      },
      {
        title: "Coldkey",
        field: "coldkey",
        minWidth: 150,
        formatter: (cell) => `<span class="mono" title="${cell.getValue()}">${shortKey(cell.getValue())}</span>`,
      },
      {
        title: "Registered",
        field: "registered_at",
        minWidth: 170,
        sorter: (a, b) => compareRegisteredAt(a, b),
        formatter: (cell) => formatLocalDateTime(cell.getValue()),
      },
      {
        title: "Reg cost (τ)",
        field: "registration_cost_tao",
        width: 110,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtTao(cell.getValue()),
      },
    ];

    function buildNote(payload) {
      const data = payload.team_rows || [];
      if (!data.length) {
        return payload.note
          ? `${payload.note} No group UIDs in the removal queue right now (immune or not eligible).`
          : "No group UIDs in the removal queue.";
      }
      const teamTop = (payload.team_rows || []).slice(0, 3);
      let note = payload.note || "";
      if (teamTop.length) {
        note += ` Next group dereg: ${formatTeamDeregList(teamTop, 3)}.`;
      }
      return note.trim();
    }

    function applySearch() {
      const q = document.getElementById("search").value.trim().toLowerCase();
      if (!table) return;
      if (!q) {
        table.clearFilter(true);
        return;
      }
      table.setFilter((data) =>
        String(data.uid).includes(q)
          || (data.team_name || "").toLowerCase().includes(q)
          || (data.hotkey || "").toLowerCase().includes(q)
          || (data.coldkey || "").toLowerCase().includes(q)
          || String(data.remove_rank ?? "").includes(q)
      );
    }

    function renderTable(payload) {
      const data = payload.team_rows || [];
      if (table) {
        table.destroy();
        table = null;
      }
      table = new Tabulator("#remove-table-wrap", {
        data,
        columns,
        layout: "fitDataStretch",
        height: "calc(100vh - 210px)",
        placeholder: "No group UIDs at risk",
        initialSort: [{ column: "remove_rank", dir: "asc" }],
        rowFormatter: (row) => {
          row.getElement().classList.toggle("row-team", !!row.getData().is_team);
        },
      });
      applySearch();
    }

    function updateSummary(payload) {
      const summary = payload.summary || {};
      document.getElementById("stat-team-risk").textContent = summary.team_at_risk ?? 0;
      document.getElementById("stat-team-rank").textContent =
        summary.best_team_remove_rank != null ? `#${summary.best_team_remove_rank}` : "-";
      document.getElementById("stat-next-team-dereg").textContent =
        formatTeamDeregList(payload.team_rows || [], 3);
      document.getElementById("stat-eligible").textContent = summary.total_eligible ?? "-";
      const fetched = payload.fetched_at || "";
      document.getElementById("stat-fetched").textContent = formatFetchedAt(fetched);
      document.getElementById("remove-note").textContent = buildNote(payload);
    }

    async function fetchData(force = false) {
      const btn = document.getElementById("refresh-btn");
      const banner = document.getElementById("error-banner");
      btn.disabled = true;
      btn.textContent = "Refreshing…";
      try {
        const url = force ? "/api/to-be-removed?force=1" : "/api/to-be-removed";
        const resp = await fetch(url);
        const payload = await resp.json();
        if (!resp.ok) throw new Error(payload.error || resp.statusText);
        renderTable(payload);
        updateSummary(payload);
        banner.classList.remove("visible");
        banner.textContent = "";
      } catch (err) {
        banner.textContent = `Fetch failed: ${err.message}`;
        banner.classList.add("visible");
      } finally {
        btn.disabled = false;
        btn.textContent = "Refresh now";
      }
    }

    function scheduleRefresh() {
      if (refreshTimer) clearInterval(refreshTimer);
      const seconds = Number(document.getElementById("refresh-interval").value);
      if (seconds > 0) {
        refreshTimer = setInterval(() => fetchData(false), seconds * 1000);
      }
    }

    document.getElementById("refresh-btn").addEventListener("click", () => fetchData(true));
    document.getElementById("search").addEventListener("input", applySearch);
    document.getElementById("refresh-interval").addEventListener("change", scheduleRefresh);

    fetchData(true);
    scheduleRefresh();
  </script>
</body>
</html>
"""


HTML_REG_SPEND = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SN15 Registration Spend by Day</title>
  <link href="/static/tabulator_midnight.min.css?v=1" rel="stylesheet">
  <script src="/static/tabulator.min.js?v=1"></script>
  <style>
""" + COMMON_STYLES + """
  </style>
</head>
<body>
  <div class="toolbar">
    <h1><span class="status-dot" id="status-dot"></span>Registration spend by day</h1>
    <a class="nav-link" href="/">Registrations</a>
    <a class="nav-link" href="/to-be-removed">To be removed</a>
    <a class="nav-link" href="/member-daily">Member daily</a>
    <label>
      Auto refresh
      <select id="refresh-interval">
        <option value="15">15s</option>
        <option value="30" selected>30s</option>
        <option value="60">60s</option>
        <option value="120">2m</option>
        <option value="0">Off</option>
      </select>
    </label>
    <button type="button" id="refresh-btn">Refresh now</button>
  </div>

  <div id="error-banner" class="error-banner"></div>

  <div class="stats">
    <span>Today total: <strong id="stat-today-total">-</strong></span>
    <span class="team-reg-stat">Group today: <strong id="stat-team-today">-</strong></span>
    <span>Days tracked: <strong id="stat-days">-</strong></span>
    <span>All-time total: <strong id="stat-all-time">-</strong></span>
    <span>All-time UIDs: <strong id="stat-all-time-uids">-</strong></span>
    <span>All-time group: <strong id="stat-all-time-team">-</strong></span>
    <span>Timezone: <strong id="stat-timezone">-</strong></span>
    <span>Updated: <strong id="stat-fetched">-</strong></span>
  </div>

  <div class="panel-note" id="spend-note">Loading…</div>

  <div id="reg-spend-table-wrap"></div>

  <script>
""" + LOCAL_TIME_JS + """
    let table = null;
    let refreshTimer = null;
    let todayDate = "";

    function fmtTao(value) {
      if (value == null || value === "") return "-";
      return Number(value).toFixed(4);
    }

    const columns = [
      {
        title: "Date",
        field: "date",
        minWidth: 140,
        sorter: "string",
        formatter: (cell) => formatDateCell(cell.getValue(), todayDate),
      },
      {
        title: "Total UIDs",
        field: "registrations",
        width: 100,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtMetricCell(cell.getValue(), "alive-cell"),
      },
      {
        title: "Total τ",
        field: "total_tao",
        width: 110,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => `<span class="total-tao-cell">${fmtTao(cell.getValue())}</span>`,
      },
      {
        title: "Group τ",
        field: "team_tao",
        width: 110,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => `<span class="team-tao-cell">${fmtTao(cell.getValue())}</span>`,
      },
      {
        title: "Group UIDs",
        field: "team_registrations",
        width: 100,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtMetricCell(cell.getValue(), "reg-fee-cell"),
      },
      {
        title: "Group share",
        field: "team_share_pct",
        width: 100,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => {
          const value = cell.getValue();
          return value == null ? "-" : `${value}%`;
        },
      },
    ];

    function renderTable(rows) {
      if (table) {
        table.destroy();
        table = null;
      }
      table = new Tabulator("#reg-spend-table-wrap", {
        data: rows,
        columns,
        layout: "fitDataStretch",
        height: "calc(100vh - 210px)",
        placeholder: "No registration spend data (need reg time + cost)",
        initialSort: [{ column: "date", dir: "desc" }],
        rowFormatter: (row) => {
          row.getElement().classList.toggle("row-today", row.getData().date === todayDate);
        },
      });
    }

    function updateSummary(payload) {
      const dailyReg = buildDailyRegistrationSpend(payload.rows || []);
      const today = dailyReg.today || {};
      todayDate = today.date || "";
      const rows = dailyReg.recent_days || [];

      document.getElementById("stat-timezone").textContent = dailyReg.timezone || LOCAL_TZ;
      document.getElementById("stat-today-total").textContent =
        `${fmtTao(today.total_tao || 0)} τ · ${today.registrations || 0} UID(s)`;
      document.getElementById("stat-team-today").textContent =
        `${fmtTao(today.team_tao || 0)} τ · ${today.team_registrations || 0} UID(s)`;
      document.getElementById("stat-days").textContent = rows.length;

      const allTimeTotal = rows.reduce((sum, row) => sum + Number(row.total_tao || 0), 0);
      const allTimeTeam = rows.reduce((sum, row) => sum + Number(row.team_tao || 0), 0);
      const allTimeUids = rows.reduce((sum, row) => sum + Number(row.registrations || 0), 0);
      const allTimeTeamUids = rows.reduce((sum, row) => sum + Number(row.team_registrations || 0), 0);
      document.getElementById("stat-all-time").textContent = `${fmtTao(allTimeTotal)} τ`;
      document.getElementById("stat-all-time-uids").textContent = String(allTimeUids);
      document.getElementById("stat-all-time-team").textContent =
        `${fmtTao(allTimeTeam)} τ · ${allTimeTeamUids} UID(s)`;

      const fetched = payload.fetched_at || "";
      document.getElementById("stat-fetched").textContent = formatFetchedAt(fetched);

      const costs = payload.registration_costs || {};
      const times = payload.registration_times || {};
      const notes = [
        `Registration burn grouped by local calendar day (${dailyReg.timezone || LOCAL_TZ}).`,
        times.note,
        costs.note,
      ].filter(Boolean);
      if (dailyReg.skipped_missing_cost) {
        notes.push(`${dailyReg.skipped_missing_cost} UID(s) skipped (missing reg cost).`);
      }
      if (dailyReg.skipped_missing_date) {
        notes.push(`${dailyReg.skipped_missing_date} UID(s) skipped (missing reg date).`);
      }
      document.getElementById("spend-note").textContent = notes.join(" | ");

      const tableRows = rows.map((row) => ({
        ...row,
        team_share_pct: row.total_tao
          ? Math.round((1000 * row.team_tao) / row.total_tao) / 10
          : 0,
      }));
      renderTable(tableRows);
    }

    async function fetchData(force = false) {
      const btn = document.getElementById("refresh-btn");
      const banner = document.getElementById("error-banner");
      btn.disabled = true;
      btn.textContent = "Refreshing…";
      try {
        const url = force ? "/api/registration-spend?force=1" : "/api/registration-spend";
        const resp = await fetch(url);
        const payload = await resp.json();
        if (!resp.ok) throw new Error(payload.error || resp.statusText);
        updateSummary(payload);
        banner.classList.remove("visible");
        banner.textContent = "";
      } catch (err) {
        banner.textContent = `Fetch failed: ${err.message}`;
        banner.classList.add("visible");
      } finally {
        btn.disabled = false;
        btn.textContent = "Refresh now";
      }
    }

    function scheduleRefresh() {
      if (refreshTimer) clearInterval(refreshTimer);
      const seconds = Number(document.getElementById("refresh-interval").value);
      if (seconds > 0) {
        refreshTimer = setInterval(() => fetchData(false), seconds * 1000);
      }
    }

    document.getElementById("refresh-btn").addEventListener("click", () => fetchData(true));
    document.getElementById("refresh-interval").addEventListener("change", scheduleRefresh);

    fetchData(true);
    scheduleRefresh();
  </script>
</body>
</html>
"""


HTML_MEMBER_DAILY = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SN15 Member Daily Stats</title>
  <link href="/static/tabulator_midnight.min.css?v=1" rel="stylesheet">
  <script src="/static/tabulator.min.js?v=1"></script>
  <style>
""" + COMMON_STYLES + """
  </style>
</head>
<body>
  <div class="toolbar">
    <h1><span class="status-dot" id="status-dot"></span>Member daily stats</h1>
    <a class="nav-link" href="/">Registrations</a>
    <a class="nav-link" href="/to-be-removed">To be removed</a>
    <a class="nav-link" href="/registration-spend">Reg spend</a>
    <label>
      Date
      <select id="date-filter"><option value="">All dates</option></select>
    </label>
    <label>
      Member
      <select id="member-filter"><option value="">All members</option></select>
    </label>
    <label>
      Auto refresh
      <select id="refresh-interval">
        <option value="15">15s</option>
        <option value="30" selected>30s</option>
        <option value="60">60s</option>
        <option value="120">2m</option>
        <option value="0">Off</option>
      </select>
    </label>
    <button type="button" id="refresh-btn">Refresh now</button>
  </div>

  <div id="error-banner" class="error-banner"></div>

  <div class="stats">
    <span>Date: <strong id="stat-selected-date">-</strong></span>
    <span>Daily TAO: <strong id="stat-daily-tao">-</strong></span>
    <span class="team-reg-stat">Reg fee: <strong id="stat-reg-fee">-</strong></span>
    <span>Reg count: <strong id="stat-reg-count">-</strong></span>
    <span>Alive UIDs: <strong id="stat-alive">-</strong></span>
    <span>Timezone: <strong id="stat-timezone">-</strong></span>
    <span>Updated: <strong id="stat-fetched">-</strong></span>
  </div>

  <div class="panel-note" id="member-note">Loading…</div>

  <div id="member-daily-table-wrap"></div>

  <script>
""" + LOCAL_TIME_JS + """
    let table = null;
    let refreshTimer = null;
    let allRows = [];
    let lastPayload = null;
    let todayDate = "";
    let memberFilter = "";
    let dateFilter = "";

    function fmtTao(value) {
      if (value == null || value === "") return "-";
      return Number(value).toFixed(4);
    }

    function memberNameFormatter(cell) {
      const value = cell.getValue();
      return value ? `<span class="team-name">${value}</span>` : "-";
    }

    function buildMemberDailyStats(rows, teamColdkeys) {
      todayDate = localDateKey(new Date());
      const members = Object.entries(teamColdkeys || {}).map(([name, coldkey]) => ({
        name,
        coldkey,
        uids: [],
      }));
      const byColdkey = Object.fromEntries(members.map((member) => [member.coldkey, member]));

      for (const row of rows || []) {
        const member = byColdkey[row.coldkey];
        if (!member) continue;
        member.uids.push({
          uid: row.uid,
          registered_at: parseRegisteredAt(row.registered_at),
          reg_cost: row.registration_cost_tao,
          daily_tao: Number(row.daily_tao || 0),
          is_validator: !!row.is_validator,
        });
      }

      const dates = new Set([todayDate]);
      for (const member of members) {
        for (const uidRow of member.uids) {
          if (uidRow.registered_at) dates.add(localDateKey(uidRow.registered_at));
        }
      }

      const out = [];
      for (const date of [...dates].sort((a, b) => b.localeCompare(a))) {
        for (const member of members) {
          const regsToday = member.uids.filter(
            (uidRow) => uidRow.registered_at && localDateKey(uidRow.registered_at) === date
          );
          const daily_reg_count = regsToday.length;
          const daily_reg_fee = regsToday.reduce(
            (sum, uidRow) => sum + (uidRow.reg_cost != null ? Number(uidRow.reg_cost) : 0),
            0
          );
          const alive = member.uids.filter(
            (uidRow) => uidRow.registered_at && localDateKey(uidRow.registered_at) <= date
          );
          const alive_uid_count = alive.length;
          const daily_tao = alive
            .filter((uidRow) => !uidRow.is_validator)
            .reduce((sum, uidRow) => sum + uidRow.daily_tao, 0);

          if (date !== todayDate && daily_reg_count === 0 && alive_uid_count === 0) continue;

          out.push({
            date,
            member: member.name,
            coldkey: member.coldkey,
            is_today: date === todayDate,
            daily_tao: Math.round(daily_tao * 1e6) / 1e6,
            daily_reg_fee: Math.round(daily_reg_fee * 1e6) / 1e6,
            daily_reg_count,
            alive_uid_count,
          });
        }
      }
      return out;
    }

    const columns = [
      {
        title: "Date",
        field: "date",
        width: 130,
        sorter: "string",
        formatter: (cell) => formatDateCell(cell.getValue(), todayDate),
      },
      { title: "Member", field: "member", width: 110, formatter: memberNameFormatter },
      {
        title: "Daily TAO (τ/day)",
        field: "daily_tao",
        width: 130,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtMetricCell(cell.getValue(), "daily-tao-cell", fmtTao),
      },
      {
        title: "Reg fee (τ)",
        field: "daily_reg_fee",
        width: 110,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtMetricCell(cell.getValue(), "reg-fee-cell", fmtTao),
      },
      {
        title: "Reg count",
        field: "daily_reg_count",
        width: 95,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtMetricCell(cell.getValue(), "reg-fee-cell"),
      },
      {
        title: "Alive UIDs",
        field: "alive_uid_count",
        width: 100,
        hozAlign: "right",
        sorter: "number",
        formatter: (cell) => fmtMetricCell(cell.getValue(), "alive-cell"),
      },
      {
        title: "Coldkey",
        field: "coldkey",
        minWidth: 150,
        formatter: (cell) => {
          const value = cell.getValue() || "";
          const short = value.length > 16 ? value.slice(0, 8) + "…" + value.slice(-6) : value;
          return `<span class="mono" title="${value}">${short || "-"}</span>`;
        },
      },
    ];

    function filteredRows() {
      return allRows.filter((row) => {
        if (dateFilter && row.date !== dateFilter) return false;
        if (memberFilter && row.member !== memberFilter) return false;
        return true;
      });
    }

    function summaryRows() {
      const summaryDate = dateFilter || todayDate;
      return allRows.filter((row) => {
        if (row.date !== summaryDate) return false;
        if (memberFilter && row.member !== memberFilter) return false;
        return true;
      });
    }

    function formatSelectedDateLabel() {
      if (!dateFilter) return "All dates";
      return dateFilter === todayDate ? `${dateFilter} (today)` : dateFilter;
    }

    function renderTable() {
      const data = filteredRows();
      if (table) {
        table.setData(data);
        if (dateFilter) table.hideColumn("date");
        else table.showColumn("date");
        return;
      }
      table = new Tabulator("#member-daily-table-wrap", {
        data,
        columns,
        layout: "fitDataStretch",
        height: "calc(100vh - 210px)",
        placeholder: "No member daily stats",
        initialSort: [
          { column: "date", dir: "desc" },
          { column: "member", dir: "asc" },
        ],
        rowFormatter: (row) => {
          const data = row.getData();
          const el = row.getElement();
          const prev = row.getPrevRow();
          el.classList.toggle("row-date-divider", !prev || prev.getData().date !== data.date);
        },
      });
      if (dateFilter) table.hideColumn("date");
    }

    function updateMemberFilterOptions(teamColdkeys) {
      const select = document.getElementById("member-filter");
      const current = select.value;
      const names = Object.keys(teamColdkeys || {}).sort((a, b) => a.localeCompare(b));
      select.innerHTML = '<option value="">All members</option>';
      for (const name of names) {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        select.appendChild(opt);
      }
      if (current && names.includes(current)) select.value = current;
      memberFilter = select.value;
    }

    function updateDateFilterOptions() {
      const select = document.getElementById("date-filter");
      const current = select.value;
      const dates = [...new Set(allRows.map((row) => row.date))].sort((a, b) => b.localeCompare(a));
      select.innerHTML = '<option value="">All dates</option>';
      for (const date of dates) {
        const opt = document.createElement("option");
        opt.value = date;
        opt.textContent = date === todayDate ? `${date} (today)` : date;
        select.appendChild(opt);
      }
      if (current && (current === "" || dates.includes(current))) {
        select.value = current;
      } else if (todayDate && dates.includes(todayDate)) {
        select.value = todayDate;
      } else {
        select.value = "";
      }
      dateFilter = select.value;
    }

    function updateSummary(payload) {
      const rows = summaryRows();
      const dailyTao = rows.reduce((sum, row) => sum + Number(row.daily_tao || 0), 0);
      const regFee = rows.reduce((sum, row) => sum + Number(row.daily_reg_fee || 0), 0);
      const regCount = rows.reduce((sum, row) => sum + Number(row.daily_reg_count || 0), 0);
      const alive = rows.reduce((sum, row) => sum + Number(row.alive_uid_count || 0), 0);

      document.getElementById("stat-selected-date").textContent = formatSelectedDateLabel();
      document.getElementById("stat-daily-tao").textContent = `${fmtTao(dailyTao)} τ/day`;
      document.getElementById("stat-reg-fee").textContent = `${fmtTao(regFee)} τ`;
      document.getElementById("stat-reg-count").textContent = String(regCount);
      document.getElementById("stat-alive").textContent = String(alive);
      document.getElementById("stat-timezone").textContent = LOCAL_TZ;
      document.getElementById("stat-fetched").textContent = formatFetchedAt(payload?.fetched_at || "");

      const emission = payload?.emission || {};
      const summaryHint = dateFilter
        ? `Stats for ${formatSelectedDateLabel()}${memberFilter ? ` · ${memberFilter}` : ""}.`
        : `Summary uses today (${todayDate}) when all dates are shown${memberFilter ? ` · ${memberFilter}` : ""}.`;
      const notes = [
        summaryHint,
        `Daily TAO uses current miner emission rates (validators excluded). Alive UIDs = cumulative registrations through the selected day.`,
        emission.miners_only ? "Emission excludes validators." : "",
      ].filter(Boolean);
      document.getElementById("member-note").textContent = notes.join(" ");
    }

    function refreshView() {
      renderTable();
      updateSummary(lastPayload);
    }

    async function fetchData(force = false) {
      const btn = document.getElementById("refresh-btn");
      const banner = document.getElementById("error-banner");
      btn.disabled = true;
      btn.textContent = "Refreshing…";
      try {
        const url = force ? "/api/registrations?force=1" : "/api/registrations";
        const resp = await fetch(url);
        const payload = await resp.json();
        if (!resp.ok) throw new Error(payload.error || resp.statusText);

        lastPayload = payload;
        updateMemberFilterOptions(payload.team_coldkeys || {});
        allRows = buildMemberDailyStats(payload.rows || [], payload.team_coldkeys || {});
        updateDateFilterOptions();
        refreshView();
        banner.classList.remove("visible");
        banner.textContent = "";
      } catch (err) {
        banner.textContent = `Fetch failed: ${err.message}`;
        banner.classList.add("visible");
      } finally {
        btn.disabled = false;
        btn.textContent = "Refresh now";
      }
    }

    function scheduleRefresh() {
      if (refreshTimer) clearInterval(refreshTimer);
      const seconds = Number(document.getElementById("refresh-interval").value);
      if (seconds > 0) {
        refreshTimer = setInterval(() => fetchData(false), seconds * 1000);
      }
    }

    document.getElementById("refresh-btn").addEventListener("click", () => fetchData(true));
    document.getElementById("date-filter").addEventListener("change", (event) => {
      dateFilter = event.target.value;
      refreshView();
    });
    document.getElementById("member-filter").addEventListener("change", (event) => {
      memberFilter = event.target.value;
      refreshView();
    });
    document.getElementById("refresh-interval").addEventListener("change", scheduleRefresh);

    fetchData(true);
    scheduleRefresh();
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/to-be-removed")
def to_be_removed():
    return render_template_string(HTML_REMOVE)


@app.route("/registration-spend")
def registration_spend():
    return render_template_string(HTML_REG_SPEND)


@app.route("/member-daily")
def member_daily():
    return render_template_string(HTML_MEMBER_DAILY)


@app.route("/api/registrations")
def api_registrations():
    force = request.args.get("force", "").lower() in {"1", "true", "yes"}
    try:
        return jsonify(_get_payload(force=force))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/to-be-removed")
def api_to_be_removed():
    force = request.args.get("force", "").lower() in {"1", "true", "yes"}
    try:
        return jsonify(_get_remove_payload(force=force))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/registration-spend")
def api_registration_spend():
    force = request.args.get("force", "").lower() in {"1", "true", "yes"}
    try:
        payload = _get_payload(force=force)
        return jsonify(
            {
                "fetched_at": payload["fetched_at"],
                "summary": payload["summary"],
                "rows": payload["rows"],
                "daily_registration_spend": payload["daily_registration_spend"],
                "registration_costs": payload["registration_costs"],
                "registration_times": payload["registration_times"],
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def main() -> None:
    import argparse

    load_env()

    parser = argparse.ArgumentParser(description="SN15 registration monitor dashboard.")
    parser.add_argument("--port", type=int, default=5055, help="Port (default: 5055)")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind (default: 0.0.0.0)",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser")
    parser.add_argument(
        "--warm-cache",
        action="store_true",
        help="Pre-fetch all registration block timestamps (slow, run once)",
    )
    parser.add_argument(
        "--warm-cache-only",
        action="store_true",
        help="Warm the timestamp cache and exit (no dashboard)",
    )
    args = parser.parse_args()

    if args.warm_cache or args.warm_cache_only:
        print("Warming registration block timestamp cache (this may take ~30 min)…")
        meta = warm_block_time_cache()
        print(
            f"Done: {meta['cached_blocks']}/{meta['total_blocks']} blocks cached, "
            f"{meta['remaining']} remaining, {meta['errors']} errors"
        )
        if meta["remaining"]:
            print("Re-run --warm-cache-only to continue, or let the dashboard warm the rest on refresh.")
        if args.warm_cache_only:
            return

    url = f"http://127.0.0.1:{args.port}"
    print(f"SN15 Register Monitor on {url}")
    print(f"To be removed (group): {url}/to-be-removed")
    print(f"Registration spend: {url}/registration-spend")
    print(f"Member daily stats: {url}/member-daily")
    if args.host == "0.0.0.0":
        print(f"Remote access: http://<host-ip>:{args.port}")
    if not os.getenv("TAOSTATS_API_KEY") and not os.getenv("TMC_API_KEY"):
        print("Tip: registration time/cost uses TaoMarketCap public API (same source as taomarketcap.com/subnets/15/registration)")

    if not args.no_browser:
        Timer(0.8, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
