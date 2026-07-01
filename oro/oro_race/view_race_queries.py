#!/usr/bin/env python3
"""Excel-like web viewer for race problems queries CSV."""

from __future__ import annotations

import csv
import json
import os
import webbrowser
from pathlib import Path
from threading import Timer

from flask import Flask, jsonify, render_template_string, request

from query_codec import encode_query, normalize_query
from coldkey_meta import serialize_coldkey_meta, sync_coldkey_meta
from coldkey_transfers import (
    build_transfer_indexes,
    serialize_transfer_pair,
    sync_coldkey_transfer_pairs,
)
from race_db import DEFAULT_DB, get_all_coldkey_meta, get_all_winners, get_coldkey_transfer_pairs, get_stored_coldkeys, init_db
from race_fetcher import sync_race_winners
from subnet_registration import fetch_subnet_registration_stats

VALID_CATEGORIES = {"Product", "Shop", "Voucher"}

DEFAULT_CSV = Path(__file__).resolve().parent / "race-problems-queries-2026-06-22.csv"
CSV_FIELDS = [
    "id",
    "race_number",
    "category",
    "query",
    "query_code",
    "frequency",
    "appeared_race_numbers",
    "correct_answer",
    "answer_agent",
]

app = Flask(__name__, static_folder="static", static_url_path="/static")

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Race Problems Queries</title>
  <link href="/static/tabulator_midnight.min.css?v=3" rel="stylesheet">
  <script src="/static/tabulator.min.js?v=3"></script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0d0d0d;
      --panel: #141414;
      --panel-2: #1c1c1c;
      --border: #2e2e2e;
      --text: #e8e8e8;
      --muted: #9ca3af;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --header: #181818;
      --row-even: #121212;
      --row-hover: #222222;
      --row-selected: #1e3a5f;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", Calibri, Arial, sans-serif;
      background: var(--bg) !important;
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
      overflow-x: auto;
    }
    .toolbar h1 {
      margin: 0;
      font-size: 1.15rem;
      font-weight: 600;
      margin-right: auto;
      color: var(--text);
    }
    .toolbar label {
      font-size: 0.85rem;
      color: var(--muted);
    }
    .toolbar input, .toolbar select, .toolbar button {
      font: inherit;
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
    }
    .toolbar input::placeholder { color: #555; }
    .toolbar input:focus, .toolbar select:focus {
      outline: none;
      border-color: var(--accent);
    }
    .toolbar button {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
      cursor: pointer;
    }
    .toolbar button:hover { background: var(--accent-hover); }
    .toolbar button.add {
      background: #16a34a;
      border-color: #16a34a;
      font-weight: 600;
      white-space: nowrap;
    }
    .toolbar button.add:hover { background: #15803d; }
    .toolbar button.secondary {
      background: var(--panel-2);
      color: var(--text);
      border-color: var(--border);
    }
    .toolbar button.secondary:hover { background: #252525; }
    .stats {
      display: flex;
      gap: 16px;
      padding: 10px 18px;
      background: var(--panel-2);
      border-bottom: 1px solid var(--border);
      font-size: 0.88rem;
      color: var(--muted);
    }
    .stats strong { color: var(--text); }
    #table-wrap {
      margin: 12px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 4px 24px rgba(0, 0, 0, 0.5);
    }
    .tabulator,
    .tabulator .tabulator-tableholder,
    .tabulator .tabulator-table,
    .tabulator .tabulator-placeholder,
    .tabulator .tabulator-loader {
      font-size: 13px;
      border: none;
      background: var(--panel) !important;
      color: var(--text);
    }
    .tabulator .tabulator-header {
      background: var(--header) !important;
      border-bottom: 1px solid var(--border);
      color: var(--muted);
    }
    .tabulator .tabulator-header .tabulator-col {
      background: var(--header) !important;
      border-right: 1px solid var(--border);
    }
    .tabulator .tabulator-header .tabulator-col-content {
      padding: 8px;
    }
    .tabulator .tabulator-header .tabulator-col .tabulator-col-title {
      color: var(--text);
    }
    .tabulator .tabulator-header .tabulator-col.tabulator-sortable:hover {
      background: #222;
    }
    .tabulator .tabulator-row {
      background: var(--panel) !important;
      border-bottom: 1px solid var(--border);
      min-height: 34px;
      color: var(--text);
    }
    .tabulator .tabulator-row.tabulator-row-even {
      background: var(--row-even) !important;
    }
    .tabulator .tabulator-row:hover {
      background: var(--row-hover) !important;
    }
    .tabulator .tabulator-row.tabulator-selected {
      background: var(--row-selected) !important;
    }
    .tabulator .tabulator-cell {
      background: transparent !important;
      border-right: 1px solid var(--border);
      padding: 6px 8px;
      color: var(--text);
    }
    .tabulator .tabulator-footer {
      background: var(--header) !important;
      border-top: 1px solid var(--border);
      color: var(--muted);
    }
    .tabulator .tabulator-footer .tabulator-page {
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
    }
    .tabulator .tabulator-footer .tabulator-page.active {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    .tabulator .tabulator-footer .tabulator-page:not(.disabled):hover {
      background: #252525;
      color: var(--text);
    }
    .tabulator .tabulator-footer .tabulator-paginator label {
      color: var(--muted);
    }
    .tabulator .tabulator-footer .tabulator-page-size {
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
    }
    .tabulator .tabulator-header-filter input,
    .tabulator .tabulator-header-filter select {
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
    }
    .tabulator .tabulator-placeholder {
      background: var(--panel);
      color: var(--muted);
    }
    .tabulator .tabulator-alert {
      background: rgba(0, 0, 0, 0.85);
    }
    .freq-high { color: #fbbf24; font-weight: 700; }
    .freq-med { color: #60a5fa; font-weight: 600; }
    .freq-low { color: var(--muted); }
    .race-list { color: #93c5fd; font-size: 12px; }
    .query-code { color: #fbbf24; font-family: Consolas, monospace; font-weight: 700; letter-spacing: 0.05em; }
    .answer-cell {
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.35;
      max-height: 120px;
      overflow: auto;
      color: #86efac;
    }
    .answer-empty { color: #555; font-style: italic; font-size: 12px; }
    .tabulator .tabulator-cell.tabulator-editing textarea {
      background: #1c1c1c;
      color: #e8e8e8;
      border: 1px solid var(--accent);
      border-radius: 4px;
      padding: 6px;
      min-height: 80px;
      font: inherit;
    }
    .save-status {
      font-size: 0.85rem;
      color: var(--muted);
      min-width: 90px;
    }
    .save-status.ok { color: #4ade80; }
    .save-status.err { color: #f87171; }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-product { background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.3); }
    .badge-shop { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge-voucher { background: rgba(236, 72, 153, 0.15); color: #f472b6; border: 1px solid rgba(236, 72, 153, 0.3); }
    .query-cell {
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.35;
      max-height: 120px;
      overflow: auto;
      color: #d4d4d4;
    }
    .query-cell::-webkit-scrollbar { width: 6px; height: 6px; }
    .query-cell::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
    .footer-note {
      padding: 10px 18px 18px;
      font-size: 0.82rem;
      color: var(--muted);
    }
    .modal-backdrop {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.72);
      z-index: 100;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      width: min(560px, 100%);
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55);
      padding: 20px 22px;
    }
    .modal h2 {
      margin: 0 0 16px;
      font-size: 1.1rem;
      font-weight: 600;
    }
    .modal-form {
      display: grid;
      gap: 12px;
    }
    .modal-form label {
      display: grid;
      gap: 6px;
      font-size: 0.85rem;
      color: var(--muted);
    }
    .modal-form input,
    .modal-form select,
    .modal-form textarea {
      font: inherit;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      width: 100%;
    }
    .modal-form textarea {
      min-height: 90px;
      resize: vertical;
      line-height: 1.4;
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 18px;
    }
    .modal-error {
      color: #f87171;
      font-size: 0.85rem;
      min-height: 1.2em;
    }
    .dup-stats-panel {
      display: none;
      margin: 0 12px 12px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }
    .dup-stats-panel.open { display: block; }
    .dup-stats-panel.fullscreen {
      display: flex;
      flex-direction: column;
      position: fixed;
      inset: 0;
      z-index: 200;
      margin: 0;
      border-radius: 0;
      border: none;
    }
    .dup-stats-header {
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: center;
      padding: 12px 16px;
      background: var(--panel-2);
      border-bottom: 1px solid var(--border);
      font-size: 0.88rem;
    }
    .dup-stats-header h2 {
      margin: 0;
      font-size: 0.95rem;
      font-weight: 600;
      margin-right: auto;
    }
    .dup-stats-header strong { color: var(--text); }
    .dup-stats-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .dup-stats-body {
      max-height: 420px;
      overflow: auto;
    }
    .dup-stats-panel.fullscreen .dup-stats-body {
      max-height: none;
      flex: 1;
      overflow: auto;
      padding: 0 12px 12px;
    }
    .dup-stats-subtitle {
      margin: 0;
      padding: 10px 12px 6px;
      font-size: 0.86rem;
      font-weight: 600;
      color: var(--muted);
      border-top: 1px solid var(--border);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .dup-stats-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.84rem;
    }
    .dup-stats-table th,
    .dup-stats-table td {
      padding: 7px 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      white-space: nowrap;
    }
    .dup-stats-table th {
      position: sticky;
      top: 0;
      background: var(--header);
      color: var(--muted);
      font-weight: 600;
      z-index: 1;
    }
    .dup-stats-table tr:hover td { background: var(--row-hover); }
    .dup-bar-wrap {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 180px;
    }
    .dup-bar {
      flex: 1;
      height: 10px;
      background: #2a2a2a;
      border-radius: 999px;
      overflow: hidden;
    }
    .dup-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, #3b82f6, #f59e0b);
      border-radius: 999px;
    }
    .dup-pct { min-width: 42px; text-align: right; font-weight: 600; }
    .dup-pct.high { color: #fbbf24; }
    .dup-pct.med { color: #60a5fa; }
    .dup-pct.low { color: var(--muted); }
    .winner-name { color: #4ade80; font-weight: 600; }
    .key-cell {
      font-family: Consolas, monospace;
      font-size: 11px;
      color: #93c5fd;
      max-width: 150px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    a.key-link {
      color: #93c5fd;
      text-decoration: none;
      cursor: pointer;
    }
    a.key-link:hover {
      color: #60a5fa;
      text-decoration: underline;
    }
    .dup-stats-panel.fullscreen .key-cell {
      max-width: none;
      white-space: normal;
      word-break: break-all;
    }
    .freq-wins { color: #fbbf24; font-weight: 700; }
    .freq-pct { color: #60a5fa; font-weight: 600; }
    .transfer-pair { color: #c4b5fd; font-size: 12px; }
    .winner-link { color: #fbbf24; }
    body.dup-fullscreen { overflow: hidden; }
    .toolbar button.stats-btn.active {
      background: #7c3aed;
      border-color: #7c3aed;
    }
    .toolbar button.sn15-btn.active {
      background: #0891b2;
      border-color: #0891b2;
    }
    .sn15-stats-panel {
      display: none;
      margin: 0 12px 12px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }
    .sn15-stats-panel.open { display: block; }
    .sn15-stats-panel.fullscreen {
      display: flex;
      flex-direction: column;
      position: fixed;
      inset: 0;
      z-index: 200;
      margin: 0;
      border-radius: 0;
      border: none;
    }
    .sn15-stats-header {
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: center;
      padding: 12px 16px;
      background: var(--panel-2);
      border-bottom: 1px solid var(--border);
      font-size: 0.88rem;
    }
    .sn15-stats-header h2 {
      margin: 0;
      font-size: 0.95rem;
      font-weight: 600;
      margin-right: auto;
    }
    .sn15-stats-header strong { color: var(--text); }
    .sn15-stats-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .sn15-stats-body {
      max-height: 420px;
      overflow: auto;
    }
    .sn15-stats-panel.fullscreen .sn15-stats-body {
      max-height: none;
      flex: 1;
      overflow: auto;
      padding: 0 12px 12px;
    }
    .sn15-stats-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.84rem;
    }
    .sn15-stats-table th,
    .sn15-stats-table td {
      padding: 7px 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }
    .sn15-stats-table th {
      position: sticky;
      top: 0;
      background: var(--header);
      color: var(--muted);
      font-weight: 600;
      z-index: 1;
      white-space: nowrap;
    }
    .sn15-stats-table tr:hover td { background: var(--row-hover); }
    .sn15-stats-table td.uid-list,
    .sn15-stats-table td.hotkey-list {
      font-family: Consolas, monospace;
      font-size: 11px;
      color: #93c5fd;
      white-space: normal;
      word-break: break-all;
      max-width: 280px;
    }
    .sn15-stats-panel.fullscreen .sn15-stats-table td.uid-list,
    .sn15-stats-panel.fullscreen .sn15-stats-table td.hotkey-list {
      max-width: none;
    }
    .uid-count-high { color: #fbbf24; font-weight: 700; }
    .uid-count-med { color: #60a5fa; font-weight: 600; }
    .uid-count-low { color: var(--muted); }
    body.sn15-fullscreen { overflow: hidden; }
    .sn15-stats-subtitle {
      margin: 0;
      padding: 10px 12px 6px;
      font-size: 0.86rem;
      font-weight: 600;
      color: var(--muted);
      border-top: 1px solid var(--border);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .sn15-tab-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 10px 12px;
      border-top: 1px solid var(--border);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .sn15-tab-row button {
      font: inherit;
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--panel-2);
      color: var(--muted);
      cursor: pointer;
    }
    .sn15-tab-row button.active {
      background: #0891b2;
      border-color: #0891b2;
      color: white;
    }
    .sn15-team-row td { background: rgba(8, 145, 178, 0.08); }
    .sn15-team-row.unregistered td { background: rgba(248, 113, 113, 0.08); }
    .badge-registered { background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.3); }
    .badge-missing { background: rgba(248, 113, 113, 0.15); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.3); }
    .rank-cell { color: #c4b5fd; font-weight: 600; white-space: nowrap; }
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: var(--bg); }
    ::-webkit-scrollbar-thumb { background: #333; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #444; }

    /* Force dark everywhere — overrides Tabulator defaults */
    html, body, .app-shell, #table-wrap, #query-table {
      background-color: #0d0d0d !important;
    }
    .tabulator,
    .tabulator *:not(input):not(select):not(option):not(button) {
      background-color: inherit;
    }
    .tabulator {
      background-color: #141414 !important;
    }
    .tabulator .tabulator-tableholder,
    .tabulator .tabulator-table,
    .tabulator .tabulator-header,
    .tabulator .tabulator-header-contents,
    .tabulator .tabulator-footer,
    .tabulator .tabulator-placeholder,
    .tabulator .tabulator-loader {
      background-color: #141414 !important;
    }
    .tabulator .tabulator-header,
    .tabulator .tabulator-header .tabulator-col {
      background-color: #181818 !important;
    }
    .tabulator .tabulator-row {
      background-color: #141414 !important;
    }
    .tabulator .tabulator-row.tabulator-row-even {
      background-color: #121212 !important;
    }
    .tabulator .tabulator-row:hover {
      background-color: #222222 !important;
    }
    .tabulator .tabulator-cell {
      background-color: transparent !important;
      color: #e8e8e8 !important;
    }
    .tabulator .tabulator-footer .tabulator-paginator,
    .tabulator .tabulator-footer .tabulator-page-counter {
      color: #9ca3af !important;
    }
  </style>
</head>
<body style="background-color:#0d0d0d;color:#e8e8e8;margin:0;">
  <div class="app-shell" style="background-color:#0d0d0d;min-height:100vh;">
  <div class="toolbar">
    <h1>Race Problems Queries</h1>
    <button type="button" class="add" id="add-row">+ Add Row</button>
    <button type="button" class="secondary stats-btn" id="toggle-dup-stats">Dup Stats</button>
    <button type="button" class="secondary sn15-btn" id="toggle-sn15-stats">SN15 UIDs</button>
    <label>
      Search
      <input id="global-search" type="search" placeholder="Filter all columns..." style="min-width:220px">
    </label>
    <label>
      Race
      <select id="race-filter"><option value="">All</option></select>
    </label>
    <label>
      Category
      <select id="category-filter">
        <option value="">All</option>
        <option value="Product">Product</option>
        <option value="Shop">Shop</option>
        <option value="Voucher">Voucher</option>
      </select>
    </label>
    <label>
      Min frequency
      <input id="freq-min" type="number" min="1" value="1" style="width:70px">
    </label>
    <label>
      <input id="unanswered-only" type="checkbox"> Unanswered only
    </label>
    <span id="save-status" class="save-status"></span>
    <button type="button" id="export-csv">Export CSV</button>
    <button type="button" class="secondary" id="reset-filters">Reset</button>
  </div>

  <div class="stats">
    <span>Rows: <strong id="stat-rows">-</strong></span>
    <span>Filtered: <strong id="stat-filtered">-</strong></span>
    <span>Unique queries: <strong id="stat-unique">-</strong></span>
    <span>Answered: <strong id="stat-answered">-</strong></span>
    <span id="race-answered-stat" style="display:none">Race answered: <strong id="stat-race-answered">-</strong></span>
    <span id="race-winner-stat" style="display:none">Winner: <strong id="stat-winner">-</strong></span>
    <span>File: <strong>{{ csv_name }}</strong></span>
  </div>

  <div id="dup-stats-panel" class="dup-stats-panel">
    <div class="dup-stats-header">
      <h2>Duplicate rate per race (from previous races)</h2>
      <span>Avg: <strong id="dup-avg">-</strong></span>
      <span>Latest: <strong id="dup-latest">-</strong></span>
      <span>Latest answered: <strong id="dup-latest-answered">-</strong></span>
      <span>Latest known dups: <strong id="dup-latest-known">-</strong></span>
      <span>Races: <strong id="dup-race-count">-</strong></span>
      <span>Winners cached: <strong id="dup-winners-cached">-</strong></span>
      <span>Transfer pairs: <strong id="dup-transfer-pairs">-</strong></span>
      <span>Top coldkey: <strong id="dup-top-coldkey">-</strong></span>
      <div class="dup-stats-actions">
        <button type="button" class="secondary" id="dup-fullscreen-btn">Fullscreen</button>
        <button type="button" class="secondary" id="dup-close-btn">Close</button>
      </div>
    </div>
    <div class="dup-stats-body">
      <table class="dup-stats-table">
        <thead>
          <tr>
            <th>Race</th>
            <th>Total</th>
            <th>Duplicate</th>
            <th>New</th>
            <th>Answered %</th>
            <th>Duplicate %</th>
            <th>Known dups %</th>
            <th>Agents</th>
            <th>Wins</th>
            <th>Coldkey</th>
            <th>Created</th>
            <th>First Win</th>
            <th>Transfer Pair</th>
          </tr>
        </thead>
        <tbody id="dup-stats-rows"></tbody>
      </table>

      <h3 class="dup-stats-subtitle">Winner coldkey transfer pairs</h3>
      <table class="dup-stats-table">
        <thead>
          <tr>
            <th>Coldkey A</th>
            <th>Coldkey B</th>
            <th>A → B</th>
            <th>B → A</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody id="winner-transfer-pairs-rows"></tbody>
      </table>
    </div>
  </div>

  <div id="sn15-stats-panel" class="sn15-stats-panel">
    <div class="sn15-stats-header">
      <h2>Subnet 15 registrations by coldkey</h2>
      <span>Registered: <strong id="sn15-registered">-</strong></span>
      <span>Coldkeys: <strong id="sn15-coldkeys">-</strong></span>
      <span>Multi-UID: <strong id="sn15-multi">-</strong></span>
      <span>Max UIDs/coldkey: <strong id="sn15-max">-</strong></span>
      <span>Avg UIDs/coldkey: <strong id="sn15-avg">-</strong></span>
      <span>Top5 UID share: <strong id="sn15-top5-uids">-</strong></span>
      <span>Top5 emission: <strong id="sn15-top5-emission">-</strong></span>
      <span>Subnet daily TAO: <strong id="sn15-total-daily-tao">-</strong></span>
      <span>Top5 daily TAO: <strong id="sn15-top5-daily-tao">-</strong></span>
      <span>Current reg cost: <strong id="sn15-current-reg-cost">-</strong></span>
      <span>Reg costs loaded: <strong id="sn15-reg-cost-loaded" title="">-</strong></span>
      <span>Group: <strong id="sn15-team-summary">-</strong></span>
      <span>Updated: <strong id="sn15-fetched">-</strong></span>
      <div class="sn15-stats-actions">
        <button type="button" class="secondary" id="sn15-refresh-btn">Refresh</button>
        <button type="button" class="secondary" id="sn15-refresh-times-btn" title="Also fetch first-registration timestamps from archive (slow)">Refresh + reg times</button>
        <button type="button" class="secondary" id="sn15-fullscreen-btn">Fullscreen</button>
        <button type="button" class="secondary" id="sn15-close-btn">Close</button>
      </div>
    </div>
    <div class="sn15-stats-body">
      <h3 class="sn15-stats-subtitle">Group status</h3>
      <table class="sn15-stats-table">
        <thead>
          <tr>
            <th>Member</th>
            <th>Status</th>
            <th>UIDs</th>
            <th>UID rank</th>
            <th>UID share</th>
            <th>Emission rank</th>
            <th>Emission</th>
            <th>Emission %</th>
            <th>Daily TAO</th>
            <th>Daily TAO %</th>
            <th>Stake rank</th>
            <th>Stake</th>
            <th>Total reg cost</th>
            <th>Avg reg cost</th>
            <th>Active</th>
            <th>First reg</th>
            <th>Coldkey</th>
            <th>UID list</th>
          </tr>
        </thead>
        <tbody id="sn15-team-rows"></tbody>
      </table>

      <h3 class="sn15-stats-subtitle">Group hotkey registration costs</h3>
      <table class="sn15-stats-table">
        <thead>
          <tr>
            <th>Member</th>
            <th>UID</th>
            <th>Reg cost (TAO)</th>
            <th>Registered</th>
            <th>Emission</th>
            <th>Daily TAO</th>
            <th>Stake</th>
            <th>Hotkey</th>
          </tr>
        </thead>
        <tbody id="sn15-team-hotkey-rows"></tbody>
      </table>

      <h3 class="sn15-stats-subtitle">UID count distribution</h3>
      <table class="sn15-stats-table">
        <thead>
          <tr>
            <th>Bucket</th>
            <th>Coldkeys</th>
            <th>Coldkey %</th>
            <th>UIDs</th>
            <th>UID %</th>
            <th>Emission</th>
            <th>Daily TAO</th>
            <th>Stake</th>
          </tr>
        </thead>
        <tbody id="sn15-distribution-rows"></tbody>
      </table>

      <div class="sn15-tab-row" id="sn15-perspective-tabs">
        <button type="button" class="active" data-perspective="by_uid_count">By UID count</button>
        <button type="button" data-perspective="by_emission">By emission</button>
        <button type="button" data-perspective="by_daily_tao">By daily TAO</button>
        <button type="button" data-perspective="by_stake">By stake</button>
        <button type="button" data-perspective="by_incentive">By incentive</button>
        <button type="button" data-perspective="by_reg_cost">By reg cost</button>
        <button type="button" data-perspective="team_only">Group only</button>
      </div>
      <table class="sn15-stats-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Coldkey</th>
            <th>UIDs</th>
            <th>UID rank</th>
            <th>UID share</th>
            <th>Active</th>
            <th>Stake</th>
            <th>Stake rank</th>
            <th>Stake %</th>
            <th>Incentive</th>
            <th>Incentive rank</th>
            <th>Emission</th>
            <th>Emission rank</th>
            <th>Emission %</th>
            <th>Daily TAO</th>
            <th>Daily TAO rank</th>
            <th>Daily TAO %</th>
            <th>Total reg cost</th>
            <th>Avg reg cost</th>
            <th>Reg rank</th>
            <th>UID list</th>
            <th>Hotkeys</th>
          </tr>
        </thead>
        <tbody id="sn15-stats-rows"></tbody>
      </table>
    </div>
  </div>

  <div id="table-wrap">
    <div id="query-table"></div>
  </div>

  <p class="footer-note">
    Tip: click Correct Answer to edit (scoped to this race+query row). Double-click Race, Category, Query, or Solver to edit those fields. Solver shows which agent passed validator SUCCESS for that answer.
  </p>
  </div>

  <div id="add-row-modal" class="modal-backdrop" aria-hidden="true">
    <div class="modal" role="dialog" aria-labelledby="add-row-title">
      <h2 id="add-row-title">Add Row</h2>
      <form id="add-row-form" class="modal-form">
        <label>
          Race number
          <input id="add-race-number" type="number" min="0" required placeholder="0 = qualifying, e.g. 35">
        </label>
        <label>
          Category
          <select id="add-category" required>
            <option value="">Select category</option>
            <option value="Product">Product</option>
            <option value="Shop">Shop</option>
            <option value="Voucher">Voucher</option>
          </select>
        </label>
        <label>
          Query
          <textarea id="add-query" required placeholder="Enter the full query text"></textarea>
        </label>
        <label>
          Correct answer (optional)
          <textarea id="add-answer" placeholder="Leave blank to reuse an existing answer for the same query"></textarea>
        </label>
        <p id="add-row-error" class="modal-error"></p>
        <div class="modal-actions">
          <button type="button" class="secondary" id="add-row-cancel">Cancel</button>
          <button type="submit" id="add-row-save">Save Row</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    function forceDarkTheme() {
      document.documentElement.style.backgroundColor = "#0d0d0d";
      document.body.style.backgroundColor = "#0d0d0d";
      document.querySelectorAll(".tabulator, .tabulator-tableholder, .tabulator-table, .tabulator-header, .tabulator-footer, .tabulator-row").forEach((el) => {
        if (el.classList.contains("tabulator-row-even")) {
          el.style.backgroundColor = "#121212";
        } else if (el.classList.contains("tabulator-header") || el.classList.contains("tabulator-col")) {
          el.style.backgroundColor = "#181818";
        } else if (el.classList.contains("tabulator-row")) {
          el.style.backgroundColor = "#141414";
        } else {
          el.style.backgroundColor = "#141414";
        }
      });
    }
    const table = new Tabulator("#query-table", {
      height: "calc(100vh - 170px)",
      layout: "fitColumns",
      placeholder: "No matching rows",
      initialSort: [{ column: "race_number", dir: "desc" }],
      pagination: true,
      paginationSize: 50,
      paginationSizeSelector: [25, 50, 100, 250, 500],
      movableColumns: true,
      resizableRows: true,
      selectable: 1,
      editTriggerEvent: "manual",
      columns: [
        { title: "#", formatter: "rownum", width: 55, hozAlign: "center", headerSort: false },
        { field: "id", visible: false },
        {
          title: "Race",
          field: "race_number",
          width: 80,
          hozAlign: "center",
          sorter: "number",
          editor: "number",
          editorParams: { min: 1, step: 1 },
          headerFilter: "input",
        },
        {
          title: "Category",
          field: "category",
          width: 110,
          editor: "list",
          editorParams: { values: ["Product", "Shop", "Voucher"] },
          formatter: (cell) => {
            const v = cell.getValue();
            const cls = v === "Product" ? "badge-product" : v === "Shop" ? "badge-shop" : "badge-voucher";
            return `<span class="badge ${cls}">${v}</span>`;
          },
          headerFilter: "list",
          headerFilterParams: { values: { "": "All", Product: "Product", Shop: "Shop", Voucher: "Voucher" } },
        },
        {
          title: "Code",
          field: "query_code",
          width: 65,
          hozAlign: "center",
          sorter: "string",
          formatter: (cell) => `<span class="query-code">${escapeHtml(cell.getValue() || "")}</span>`,
          headerFilter: "input",
        },
        {
          title: "Frequency",
          field: "frequency",
          width: 95,
          hozAlign: "center",
          sorter: "number",
          formatter: (cell) => {
            const v = cell.getValue();
            const cls = v >= 4 ? "freq-high" : v >= 2 ? "freq-med" : "freq-low";
            return `<span class="${cls}">${v}</span>`;
          },
        },
        {
          title: "Appeared Races",
          field: "appeared_race_numbers",
          width: 140,
          hozAlign: "left",
          sorter: "string",
          formatter: (cell) => `<span class="race-list">${escapeHtml(cell.getValue() || "")}</span>`,
          headerFilter: "input",
        },
        {
          title: "Query",
          field: "query",
          minWidth: 280,
          widthGrow: 2,
          editor: "textarea",
          editorParams: { verticalNavigation: "editor", shiftEnterSubmit: true },
          formatter: (cell) => `<div class="query-cell">${escapeHtml(cell.getValue() || "")}</div>`,
          headerFilter: "input",
        },
        {
          title: "Correct Answer",
          field: "correct_answer",
          minWidth: 220,
          widthGrow: 2,
          editor: "textarea",
          editorParams: { verticalNavigation: "editor", shiftEnterSubmit: true },
          formatter: (cell) => {
            const v = cell.getValue();
            if (!v || !String(v).trim()) return '<span class="answer-empty">Click to add...</span>';
            return `<div class="answer-cell">${escapeHtml(v)}</div>`;
          },
          headerFilter: "input",
        },
        {
          title: "Solver",
          field: "answer_agent",
          width: 130,
          editor: "input",
          formatter: (cell) => {
            const v = cell.getValue();
            if (!v || !String(v).trim()) return '<span class="answer-empty">-</span>';
            return `<span class="winner-name">${escapeHtml(v)}</span>`;
          },
          headerFilter: "input",
        },
      ],
    });

    function escapeHtml(text) {
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function coldkeyAccountUrl(coldkey) {
      return `https://taostats.io/account/${encodeURIComponent(coldkey)}`;
    }

    function formatColdkeyLink(coldkey) {
      if (!coldkey || coldkey === "-") return escapeHtml(coldkey || "-");
      const url = coldkeyAccountUrl(coldkey);
      return `<a class="key-link key-cell" href="${url}" target="_blank" rel="noopener noreferrer" title="View coldkey created date on taostats">${escapeHtml(coldkey)}</a>`;
    }

    function applyFilters() {
      const search = document.getElementById("global-search").value.trim().toLowerCase();
      const race = document.getElementById("race-filter").value;
      const category = document.getElementById("category-filter").value;
      const freqMin = Number(document.getElementById("freq-min").value || 1);

      table.setFilter((row) => {
        if (race && String(row.race_number) !== race) return false;
        if (category && row.category !== category) return false;
        if (Number(row.frequency) < freqMin) return false;
        if (document.getElementById("unanswered-only").checked && String(row.correct_answer || "").trim()) return false;
        if (search) {
          const hay = `${row.race_number} ${row.category} ${row.query} ${row.query_code} ${row.frequency} ${row.appeared_race_numbers} ${row.correct_answer || ""} ${row.answer_agent || ""}`.toLowerCase();
          if (!hay.includes(search)) return false;
        }
        return true;
      });
      updateSelectedRaceStats(race);
    }

    function updateSelectedRaceStats(raceValue) {
      updateSelectedRaceAnswered(raceValue, table.getData());
      updateSelectedRaceWinner(raceValue);
    }

    function updateSelectedRaceAnswered(raceValue, data) {
      const stat = document.getElementById("race-answered-stat");
      const label = document.getElementById("stat-race-answered");
      if (!raceValue) {
        stat.style.display = "none";
        return;
      }
      const raceRows = data.filter((row) => String(row.race_number) === raceValue);
      const answered = raceRows.filter((row) => String(row.correct_answer || "").trim()).length;
      const total = raceRows.length;
      const pct = total ? Math.round((1000 * answered) / total) / 10 : 0;
      stat.style.display = "inline";
      label.textContent = `${pct}% (${answered}/${total})`;
    }

    function updateSelectedRaceWinner(raceValue) {
      const stat = document.getElementById("race-winner-stat");
      const label = document.getElementById("stat-winner");
      if (!raceValue || !window.raceWinnersByRace) {
        stat.style.display = "none";
        return;
      }
      const winner = window.raceWinnersByRace[Number(raceValue)];
      if (!winner) {
        stat.style.display = "inline";
        label.textContent = "not cached yet";
        return;
      }
      stat.style.display = "inline";
      const agent = escapeHtml(winner.agent_name || "?");
      if (winner.miner_coldkey) {
        const shortKey = `${winner.miner_coldkey.slice(0, 8)}...`;
        label.innerHTML = `${agent} (<a class="key-link" href="${coldkeyAccountUrl(winner.miner_coldkey)}" target="_blank" rel="noopener noreferrer" title="View coldkey created date on taostats: ${escapeHtml(winner.miner_coldkey)}">${escapeHtml(shortKey)}</a>)`;
      } else {
        label.textContent = `${winner.agent_name || "?"}`;
        label.title = winner.miner_hotkey || "";
      }
    }

    function setSaveStatus(text, kind = "") {
      const el = document.getElementById("save-status");
      el.textContent = text;
      el.className = "save-status" + (kind ? ` ${kind}` : "");
      if (text) setTimeout(() => { el.textContent = ""; el.className = "save-status"; }, 2500);
    }

    async function saveAnswer(raceNumber, query, correctAnswer) {
      const res = await fetch("/api/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ race_number: raceNumber, query, correct_answer: correctAnswer }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error || "Save failed");
      return result;
    }

    async function saveAnswerAgent(raceNumber, query, answerAgent) {
      const res = await fetch("/api/answer-agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ race_number: raceNumber, query, answer_agent: answerAgent }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error || "Save failed");
      return result;
    }

    async function saveRowField(rowId, field, value) {
      const res = await fetch("/api/row/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: rowId, [field]: value }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error || "Save failed");
      return result;
    }

    function updateStats(data) {
      document.getElementById("stat-rows").textContent = data.length;
      const unique = new Set(data.map((r) => r.query)).size;
      document.getElementById("stat-unique").textContent = unique;
      const answered = new Set(data.filter((r) => String(r.correct_answer || "").trim()).map((r) => r.query)).size;
      document.getElementById("stat-answered").textContent = `${answered} / ${unique}`;
    }

    function updateRaceFilterOptions(data) {
      const raceSelect = document.getElementById("race-filter");
      const current = raceSelect.value;
      const races = [...new Set(data.map((r) => String(r.race_number)))].sort((a, b) => Number(a) - Number(b));
      raceSelect.innerHTML = '<option value="">All</option>';
      for (const race of races) {
        const opt = document.createElement("option");
        opt.value = race;
        opt.textContent = race;
        raceSelect.appendChild(opt);
      }
      if (current && races.includes(current)) {
        raceSelect.value = current;
      }
    }

    async function reloadTableData() {
      const res = await fetch("/api/data");
      if (!res.ok) throw new Error("Failed to reload data");
      const data = await res.json();
      table.setData(data);
      updateStats(data);
      document.getElementById("stat-filtered").textContent = table.getRows("active").length;
      updateRaceFilterOptions(data);
      forceDarkTheme();
      await refreshDuplicateStatsIfOpen();
      return data;
    }

    function openAddRowModal() {
      document.getElementById("add-row-error").textContent = "";
      document.getElementById("add-row-form").reset();
      const modal = document.getElementById("add-row-modal");
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
      document.getElementById("add-race-number").focus();
    }

    function closeAddRowModal() {
      const modal = document.getElementById("add-row-modal");
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
      document.getElementById("add-row-error").textContent = "";
    }

    function setTableHeight() {
      const dupPanel = document.getElementById("dup-stats-panel");
      const sn15Panel = document.getElementById("sn15-stats-panel");
      const dupOpen = dupPanel.classList.contains("open") && !dupPanel.classList.contains("fullscreen");
      const sn15Open = sn15Panel.classList.contains("open") && !sn15Panel.classList.contains("fullscreen");
      const extra = (dupOpen ? 350 : 0) + (sn15Open ? 520 : 0);
      table.setHeight(extra ? `calc(100vh - ${170 + extra}px)` : "calc(100vh - 170px)");
    }

    function coldkeyTransfersUrl(coldkey) {
      return `https://taostats.io/account/${encodeURIComponent(coldkey)}/transfers`;
    }

    function formatCounterpartyLink(coldkey, label, title = "") {
      if (!coldkey || coldkey === "-") return escapeHtml(label || "-");
      return `<a class="key-link transfer-pair" href="${coldkeyTransfersUrl(coldkey)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(title || coldkey)}">${escapeHtml(label)}</a>`;
    }

    function formatTransferPairSummary(coldkey, pairsByColdkey, winnerColdkeys) {
      const pairs = pairsByColdkey[coldkey] || [];
      if (!pairs.length) return "-";
      const top = pairs[0];
      const total = Number(top.total_transfers || (top.in_count || 0) + (top.out_count || 0));
      const direction = top.direction === "both" ? "↔" : top.direction === "in" ? "←" : "→";
      const shortKey = `${top.counterparty.slice(0, 8)}...`;
      const winnerMark = winnerColdkeys.has(top.counterparty) ? " ★" : "";
      const label = `${direction} ${shortKey} (${total})${winnerMark}`;
      const title = `${top.direction || "transfer"} with ${top.counterparty}`;
      return formatCounterpartyLink(top.counterparty, label, title);
    }

    function renderWinnerTransferPairs(linkedPairs) {
      const tbody = document.getElementById("winner-transfer-pairs-rows");
      if (!linkedPairs.length) {
        tbody.innerHTML = '<tr><td colspan="5">No transfer pairs between winner coldkeys yet</td></tr>';
        return;
      }
      tbody.innerHTML = linkedPairs.map((pair) => `
        <tr>
          <td>${formatColdkeyLink(pair.coldkey_a)}</td>
          <td>${formatColdkeyLink(pair.coldkey_b)}</td>
          <td>${formatCounterpartyLink(pair.coldkey_b, String(pair.a_to_b), `${pair.coldkey_a} → ${pair.coldkey_b}`)}</td>
          <td>${formatCounterpartyLink(pair.coldkey_a, String(pair.b_to_a), `${pair.coldkey_b} → ${pair.coldkey_a}`)}</td>
          <td><span class="freq-wins">${pair.total_transfers}</span></td>
        </tr>
      `).join("");
    }

    function buildColdkeyWinIndex(winners) {
      const total = Object.keys(winners).length;
      const byKey = {};
      for (const winner of Object.values(winners)) {
        const key = winner.miner_coldkey || winner.miner_hotkey;
        if (!key) continue;
        if (!byKey[key]) {
          byKey[key] = { coldkey: key, wins: 0, agents: new Set() };
        }
        byKey[key].wins += 1;
        if (winner.agent_name) byKey[key].agents.add(winner.agent_name);
      }
      const index = {};
      for (const item of Object.values(byKey)) {
        index[item.coldkey] = {
          wins: item.wins,
          agents: [...item.agents].sort().join(", "),
          win_pct: total ? Math.round((1000 * item.wins) / total) / 10 : 0,
        };
      }
      return index;
    }

    function updateTopColdkeySummary(coldkeyIndex) {
      const top = Object.values(
        Object.fromEntries(
          Object.entries(coldkeyIndex).map(([coldkey, info]) => [coldkey, { coldkey, ...info }])
        )
      ).sort((a, b) => b.wins - a.wins || a.coldkey.localeCompare(b.coldkey))[0];
      const label = document.getElementById("dup-top-coldkey");
      if (!top) {
        label.textContent = "-";
        label.removeAttribute("title");
        return;
      }
      const shortKey = `${top.coldkey.slice(0, 8)}... (${top.wins} wins, ${top.win_pct}%)`;
      label.innerHTML = `<a class="key-link" href="${coldkeyAccountUrl(top.coldkey)}" target="_blank" rel="noopener noreferrer" title="View coldkey created date on taostats: ${escapeHtml(top.coldkey)}">${escapeHtml(shortKey)}</a>`;
    }

    function setDupFullscreen(enabled) {
      const panel = document.getElementById("dup-stats-panel");
      const btn = document.getElementById("dup-fullscreen-btn");
      panel.classList.toggle("fullscreen", enabled);
      document.body.classList.toggle("dup-fullscreen", enabled);
      btn.textContent = enabled ? "Exit Fullscreen" : "Fullscreen";
      setTableHeight();
    }

    function uidCountClass(count) {
      if (count >= 8) return "uid-count-high";
      if (count >= 4) return "uid-count-med";
      return "uid-count-low";
    }

    function formatHotkeyList(hotkeys) {
      if (!hotkeys.length) return "-";
      return hotkeys.map((hotkey) => `${hotkey.slice(0, 10)}...`).join(", ");
    }

    function formatRank(value) {
      return value == null ? "-" : `#${value}`;
    }

    function formatPct(value) {
      return value == null ? "-" : `${value}%`;
    }

    function formatRegCost(value) {
      return value == null || value === "" ? "-" : Number(value).toFixed(6);
    }

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

    function formatRegisteredAt(value) {
      const dt = parseRegisteredAt(value);
      if (!dt) return value ? escapeHtml(String(value)) : "-";
      return escapeHtml(dt.toLocaleString(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }));
    }

    function formatDailyTao(value) {
      return value == null || value === "" ? "-" : Number(value).toFixed(4);
    }

    function renderSn15Distribution(distribution) {
      const tbody = document.getElementById("sn15-distribution-rows");
      if (!distribution.length) {
        tbody.innerHTML = '<tr><td colspan="8">No distribution data</td></tr>';
        return;
      }
      tbody.innerHTML = distribution.map((row) => `
        <tr>
          <td>${escapeHtml(row.label)}</td>
          <td>${row.coldkeys}</td>
          <td><span class="freq-pct">${row.coldkey_pct}%</span></td>
          <td><span class="${uidCountClass(row.uids)}">${row.uids}</span></td>
          <td><span class="freq-pct">${row.uid_pct}%</span></td>
          <td>${row.total_emission.toFixed(6)}</td>
          <td><span class="freq-wins">${formatDailyTao(row.total_daily_tao)}</span></td>
          <td>${row.total_stake.toFixed(4)}</td>
        </tr>
      `).join("");
    }

    function renderSn15TeamStatus(teamRows) {
      const tbody = document.getElementById("sn15-team-rows");
      if (!teamRows.length) {
        tbody.innerHTML = '<tr><td colspan="18">No group coldkeys configured</td></tr>';
        return;
      }
      tbody.innerHTML = teamRows.map((row) => {
        const rowCls = row.registered ? "sn15-team-row" : "sn15-team-row unregistered";
        const status = row.registered
          ? '<span class="badge badge-registered">Registered</span>'
          : '<span class="badge badge-missing">Not registered</span>';
        const uidCls = uidCountClass(row.uid_count);
        return `<tr class="${rowCls}">
          <td><span class="winner-name">${escapeHtml(row.name)}</span></td>
          <td>${status}</td>
          <td><span class="${uidCls}">${row.uid_count}</span></td>
          <td><span class="rank-cell">${formatRank(row.uid_rank)}</span></td>
          <td><span class="freq-pct">${formatPct(row.uid_pct)}</span></td>
          <td><span class="rank-cell">${formatRank(row.emission_rank)}</span></td>
          <td>${row.total_emission.toFixed(6)}</td>
          <td><span class="freq-pct">${formatPct(row.emission_pct)}</span></td>
          <td><span class="freq-wins">${formatDailyTao(row.daily_tao)}</span></td>
          <td><span class="freq-pct">${formatPct(row.daily_tao_pct)}</span></td>
          <td><span class="rank-cell">${formatRank(row.stake_rank)}</span></td>
          <td>${row.total_stake.toFixed(4)}</td>
          <td><span class="freq-wins">${formatRegCost(row.total_registration_cost_tao)}</span></td>
          <td>${formatRegCost(row.avg_registration_cost_tao)}</td>
          <td>${row.active_count}/${row.uid_count || 0}</td>
          <td>${escapeHtml(row.first_registered_at || "-")}</td>
          <td>${formatColdkeyLink(row.coldkey)}</td>
          <td class="uid-list">${escapeHtml((row.uids || []).join(", ") || "-")}</td>
        </tr>`;
      }).join("");
    }

    function renderSn15TeamHotkeys(teamHotkeyRows) {
      const tbody = document.getElementById("sn15-team-hotkey-rows");
      if (!teamHotkeyRows.length) {
        tbody.innerHTML = '<tr><td colspan="8">No group hotkeys found</td></tr>';
        return;
      }
      tbody.innerHTML = teamHotkeyRows.map((row) => `
        <tr class="sn15-team-row">
          <td><span class="winner-name">${escapeHtml(row.team_name || "-")}</span></td>
          <td>${row.uid}</td>
          <td><span class="freq-wins">${formatRegCost(row.registration_cost_tao)}</span></td>
          <td>${formatRegisteredAt(row.registered_at)}</td>
          <td>${Number(row.emission || 0).toFixed(6)}</td>
          <td><span class="freq-wins">${formatDailyTao(row.daily_tao)}</span></td>
          <td>${Number(row.stake || 0).toFixed(4)}</td>
          <td class="hotkey-list">${escapeHtml(row.hotkey)}</td>
        </tr>
      `).join("");
    }

    function getSn15PerspectiveRows(payload, perspective) {
      if (perspective === "team_only") {
        return (payload.team || []).filter((row) => row.registered).map((row) => ({
          coldkey: row.coldkey,
          uid_count: row.uid_count,
          uid_rank: row.uid_rank,
          uid_pct: row.uid_pct,
          active_count: row.active_count,
          total_stake: row.total_stake,
          stake_rank: row.stake_rank,
          stake_pct: row.stake_pct,
          total_incentive: row.total_incentive,
          incentive_rank: row.incentive_rank,
          total_emission: row.total_emission,
          emission_rank: row.emission_rank,
          emission_pct: row.emission_pct,
          daily_tao: row.daily_tao,
          daily_tao_rank: row.daily_tao_rank,
          daily_tao_pct: row.daily_tao_pct,
          total_registration_cost_tao: row.total_registration_cost_tao,
          avg_registration_cost_tao: row.avg_registration_cost_tao,
          registration_cost_rank: row.registration_cost_rank,
          uids: row.uids,
          hotkeys: row.hotkeys,
          is_team: true,
        }));
      }
      if (perspective === "by_reg_cost") {
        return (payload.perspectives && payload.perspectives.by_reg_cost) || [];
      }
      return (payload.perspectives && payload.perspectives[perspective]) || payload.coldkeys || [];
    }

    function renderSn15ColdkeyTable(rows) {
      const tbody = document.getElementById("sn15-stats-rows");
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="22">No registrations found</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map((row, index) => {
        const uidCls = uidCountClass(row.uid_count);
        const rowCls = row.is_team ? "sn15-team-row" : "";
        return `<tr class="${rowCls}">
          <td>${index + 1}</td>
          <td>${formatColdkeyLink(row.coldkey)}</td>
          <td><span class="${uidCls}">${row.uid_count}</span></td>
          <td><span class="rank-cell">${formatRank(row.uid_rank)}</span></td>
          <td><span class="freq-pct">${formatPct(row.uid_pct)}</span></td>
          <td>${row.active_count}/${row.uid_count}</td>
          <td>${row.total_stake.toFixed(4)}</td>
          <td><span class="rank-cell">${formatRank(row.stake_rank)}</span></td>
          <td><span class="freq-pct">${formatPct(row.stake_pct)}</span></td>
          <td>${row.total_incentive.toFixed(6)}</td>
          <td><span class="rank-cell">${formatRank(row.incentive_rank)}</span></td>
          <td>${row.total_emission.toFixed(6)}</td>
          <td><span class="rank-cell">${formatRank(row.emission_rank)}</span></td>
          <td><span class="freq-pct">${formatPct(row.emission_pct)}</span></td>
          <td><span class="freq-wins">${formatDailyTao(row.daily_tao)}</span></td>
          <td><span class="rank-cell">${formatRank(row.daily_tao_rank)}</span></td>
          <td><span class="freq-pct">${formatPct(row.daily_tao_pct)}</span></td>
          <td><span class="freq-wins">${formatRegCost(row.total_registration_cost_tao)}</span></td>
          <td>${formatRegCost(row.avg_registration_cost_tao)}</td>
          <td><span class="rank-cell">${formatRank(row.registration_cost_rank)}</span></td>
          <td class="uid-list">${escapeHtml(row.uids.join(", "))}</td>
          <td class="hotkey-list" title="${escapeHtml(row.hotkeys.join("\\n"))}">${escapeHtml(formatHotkeyList(row.hotkeys))}</td>
        </tr>`;
      }).join("");
    }

    function renderSubnetRegistrationStats(payload) {
      const { summary, coldkeys, fetched_at: fetchedAt, distribution, team, team_hotkeys: teamHotkeys, registration_costs: registrationCosts } = payload;
      document.getElementById("sn15-registered").textContent = `${summary.registered_count}/${summary.total_slots}`;
      document.getElementById("sn15-coldkeys").textContent = summary.unique_coldkeys;
      document.getElementById("sn15-multi").textContent = summary.multi_uid_coldkeys;
      document.getElementById("sn15-max").textContent = summary.max_uid_count;
      document.getElementById("sn15-avg").textContent = summary.avg_uids_per_coldkey;
      document.getElementById("sn15-top5-uids").textContent = `${summary.top5_uid_share_pct || 0}%`;
      document.getElementById("sn15-top5-emission").textContent = `${summary.top5_emission_share_pct || 0}%`;
      document.getElementById("sn15-total-daily-tao").textContent =
        summary.total_daily_tao != null ? `${formatDailyTao(summary.total_daily_tao)} τ/day` : "-";
      document.getElementById("sn15-top5-daily-tao").textContent = `${summary.top5_daily_tao_share_pct || 0}%`;
      document.getElementById("sn15-current-reg-cost").textContent =
        summary.current_registration_cost_tao != null
          ? `${Number(summary.current_registration_cost_tao).toFixed(6)} TAO`
          : "-";
      const regLoaded = document.getElementById("sn15-reg-cost-loaded");
      const matched = registrationCosts?.matched ?? summary.known_hotkey_registration_costs ?? 0;
      const target = registrationCosts?.target_hotkeys ?? summary.registered_count ?? 0;
      regLoaded.textContent = `${matched}/${target}`;
      regLoaded.title = registrationCosts?.note || "Per-hotkey registration burn requires TAOSTATS_API_KEY";
      document.getElementById("sn15-team-summary").textContent =
        `${summary.team_registered || 0}/${summary.team_members || 0} members · ${summary.team_uids || 0} UIDs (${summary.team_uid_pct || 0}%) · ${summary.team_emission_pct || 0}% emission · ${formatDailyTao(summary.team_daily_tao)} τ/day (${summary.team_daily_tao_pct || 0}%)` +
        (summary.team_total_registration_cost_tao != null
          ? ` · ${Number(summary.team_total_registration_cost_tao).toFixed(4)} TAO spent`
          : "");
      document.getElementById("sn15-fetched").textContent = fetchedAt
        ? new Date(fetchedAt).toLocaleString(undefined, {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })
        : "-";

      renderSn15TeamStatus(team || []);
      renderSn15TeamHotkeys(teamHotkeys || []);
      renderSn15Distribution(distribution || []);

      const perspective = window.sn15Perspective || "by_uid_count";
      const rows = getSn15PerspectiveRows(payload, perspective);
      renderSn15ColdkeyTable(rows.length ? rows : coldkeys);
    }

    async function loadSubnetRegistrationStats(force = false, includeRegistrationTimes = false) {
      const panel = document.getElementById("sn15-stats-panel");
      if (!force && panel.dataset.loaded === "1" && window.subnetRegistrationStats) {
        renderSubnetRegistrationStats(window.subnetRegistrationStats);
        return window.subnetRegistrationStats;
      }
      const query = includeRegistrationTimes ? "?registration_times=1" : "";
      const res = await fetch(`/api/subnet-registrations${query}`);
      if (!res.ok) throw new Error("Failed to load subnet registration stats");
      const payload = await res.json();
      window.subnetRegistrationStats = payload;
      renderSubnetRegistrationStats(payload);
      panel.dataset.loaded = "1";
      return payload;
    }

    function setSn15Fullscreen(enabled) {
      const panel = document.getElementById("sn15-stats-panel");
      const btn = document.getElementById("sn15-fullscreen-btn");
      panel.classList.toggle("fullscreen", enabled);
      document.body.classList.toggle("sn15-fullscreen", enabled);
      btn.textContent = enabled ? "Exit Fullscreen" : "Fullscreen";
      setTableHeight();
    }

    function closeSn15Stats() {
      const panel = document.getElementById("sn15-stats-panel");
      const btn = document.getElementById("toggle-sn15-stats");
      setSn15Fullscreen(false);
      panel.classList.remove("open");
      btn.classList.remove("active");
      setTableHeight();
    }

    function closeDupStats() {
      const panel = document.getElementById("dup-stats-panel");
      const btn = document.getElementById("toggle-dup-stats");
      setDupFullscreen(false);
      panel.classList.remove("open");
      btn.classList.remove("active");
      setTableHeight();
    }

    function renderDuplicateStats(stats) {
      const { races, summary } = stats;
      const winners = window.raceWinnersByRace || {};
      const coldkeyIndex = buildColdkeyWinIndex(winners);
      const coldkeyMeta = window.coldkeyMetaByKey || {};
      const pairsByColdkey = window.transferPairsByColdkey || {};
      const winnerColdkeys = window.winnerColdkeys || new Set();
      document.getElementById("dup-avg").textContent = `${summary.avg_duplicate_pct}%`;
      const latest = races.length ? races[races.length - 1] : null;
      document.getElementById("dup-latest").textContent = latest
        ? `Race ${latest.race_number}: ${latest.duplicate_pct}%`
        : "-";
      document.getElementById("dup-latest-answered").textContent = latest
        ? `${latest.answered_pct}% (${latest.answered}/${latest.total})`
        : "-";
      document.getElementById("dup-latest-known").textContent = latest
        ? `${latest.known_total_pct}% (${latest.known_duplicates}/${latest.total})`
        : "-";
      document.getElementById("dup-race-count").textContent = summary.race_count;
      document.getElementById("dup-winners-cached").textContent = Object.keys(winners).length;
      updateTopColdkeySummary(coldkeyIndex);
      renderWinnerTransferPairs(window.winnerTransferPairs || []);

      const tbody = document.getElementById("dup-stats-rows");
      tbody.innerHTML = races.map((race) => {
        const answeredCls = race.answered_pct >= 75 ? "high" : race.answered_pct >= 40 ? "med" : "low";
        const cls = race.duplicate_pct >= 40 ? "high" : race.duplicate_pct >= 20 ? "med" : "low";
        const knownCls = race.known_total_pct >= 75 ? "high" : race.known_total_pct >= 40 ? "med" : "low";
        const answeredLabel = `${race.answered_pct}% (${race.answered}/${race.total})`;
        const dupNote = race.duplicates
          ? `${race.known_answer_pct}% of ${race.duplicates} dups`
          : "no duplicates";
        const knownLabel = `${race.known_total_pct}% (${race.known_duplicates}/${race.total})`;
        const winner = winners[race.race_number];
        const coldkey = winner?.miner_coldkey || winner?.miner_hotkey || "-";
        const coldkeyInfo = coldkey !== "-" ? coldkeyIndex[coldkey] : null;
        const agents = coldkeyInfo?.agents || winner?.agent_name || "-";
        const wins = coldkeyInfo?.wins ?? "-";
        const meta = coldkey !== "-" ? coldkeyMeta[coldkey] : null;
        const created = meta?.coldkey_created_date || "-";
        const firstWin = meta?.first_win_date || "-";
        const createdTitle = meta?.coldkey_created_at
          ? `Coldkey created: ${meta.coldkey_created_at}`
          : "Created date not cached yet";
        const firstWinTitle = meta?.first_win_at ? `Race ${meta.first_win_race || "?"} · ${meta.first_win_at}` : "";
        return `<tr>
          <td>${race.race_number}</td>
          <td>${race.total}</td>
          <td>${race.duplicates}</td>
          <td>${race.new}</td>
          <td><span class="dup-pct ${answeredCls}" title="Problems in this race with a saved correct answer.">${answeredLabel}</span></td>
          <td>
            <div class="dup-bar-wrap">
              <div class="dup-bar"><div class="dup-bar-fill" style="width:${race.duplicate_pct}%"></div></div>
              <span class="dup-pct ${cls}">${race.duplicate_pct}%</span>
            </div>
          </td>
          <td><span class="dup-pct ${knownCls}" title="Duplicate problems with known answers, as share of ${race.total}. ${dupNote}.">${knownLabel}</span></td>
          <td><span class="winner-name">${escapeHtml(agents)}</span></td>
          <td><span class="freq-wins">${wins}</span></td>
          <td>${formatColdkeyLink(coldkey)}</td>
          <td title="${escapeHtml(createdTitle)}">${escapeHtml(created)}</td>
          <td title="${escapeHtml(firstWinTitle)}">${escapeHtml(firstWin)}</td>
          <td>${formatTransferPairSummary(coldkey, pairsByColdkey, winnerColdkeys)}</td>
        </tr>`;
      }).join("");
    }

    async function loadTransferPairs() {
      const res = await fetch("/api/coldkey-transfers");
      if (!res.ok) throw new Error("Failed to load transfer pairs");
      const payload = await res.json();
      window.transferPairsByColdkey = payload.by_coldkey || {};
      window.winnerTransferPairs = payload.winner_pairs || [];
      window.winnerColdkeys = new Set(payload.winner_coldkeys || []);
      const transferLabel = document.getElementById("dup-transfer-pairs");
      if (transferLabel) {
        const count = payload.pair_count || 0;
        const winnerCount = payload.winner_pair_count || 0;
        transferLabel.textContent = `${count} (${winnerCount} winner↔winner)`;
        transferLabel.title = payload.source_note || "";
      }
      return payload;
    }

    async function loadColdkeyMeta() {
      const res = await fetch("/api/coldkey-meta");
      if (!res.ok) throw new Error("Failed to load coldkey meta");
      const rows = await res.json();
      window.coldkeyMetaByKey = Object.fromEntries(
        rows.map((item) => [item.coldkey, item])
      );
      return rows;
    }

    async function loadRaceWinners() {
      const res = await fetch("/api/race-winners");
      if (!res.ok) throw new Error("Failed to load race winners");
      const winners = await res.json();
      window.raceWinnersByRace = Object.fromEntries(
        winners.map((item) => [item.race_number, item])
      );
      return winners;
    }

    async function loadDuplicateStats() {
      const res = await fetch("/api/duplicate-stats");
      if (!res.ok) throw new Error("Failed to load duplicate stats");
      const stats = await res.json();
      renderDuplicateStats(stats);
      return stats;
    }

    async function refreshDuplicateStatsIfOpen() {
      const panel = document.getElementById("dup-stats-panel");
      await loadRaceWinners();
      await loadColdkeyMeta();
      await loadTransferPairs();
      if (!panel.classList.contains("open")) return;
      await loadDuplicateStats();
      panel.dataset.loaded = "1";
    }

    const EDIT_ON_DBLCLICK = new Set(["race_number", "category", "query", "answer_agent"]);

    table.on("cellClick", (e, cell) => {
      if (cell.getField() !== "correct_answer") return;
      const el = cell.getElement();
      if (el.classList.contains("tabulator-editing")) return;
      cell.edit();
    });

    table.on("cellDblClick", (e, cell) => {
      if (!EDIT_ON_DBLCLICK.has(cell.getField())) return;
      const el = cell.getElement();
      if (el.classList.contains("tabulator-editing")) return;
      cell.edit();
    });

    table.on("cellEdited", async (cell) => {
      const field = cell.getField();
      const row = cell.getRow().getData();
      const value = cell.getValue();

      if (field === "correct_answer") {
        const answer = value || "";
        try {
          setSaveStatus("Saving...", "");
          const result = await saveAnswer(row.race_number, row.query, answer);
          table.getRows().forEach((r) => {
            const data = r.getData();
            if (data.query === row.query && String(data.race_number) === String(row.race_number)) {
              const patch = { correct_answer: answer };
              if (!answer.trim()) patch.answer_agent = "";
              r.update(patch);
            }
          });
          setSaveStatus(`Saved to ${result.updated} row(s) for race ${row.race_number}`, "ok");
          updateStats(table.getData());
          updateSelectedRaceAnswered(document.getElementById("race-filter").value, table.getData());
        } catch (err) {
          cell.restoreOldValue();
          setSaveStatus(err.message || "Save failed", "err");
        }
        return;
      }

      if (field === "answer_agent") {
        const agent = value || "";
        try {
          setSaveStatus("Saving...", "");
          const result = await saveAnswerAgent(row.race_number, row.query, agent);
          table.getRows().forEach((r) => {
            const data = r.getData();
            if (data.query === row.query && String(data.race_number) === String(row.race_number)) {
              r.update({ answer_agent: agent });
            }
          });
          setSaveStatus(`Saved solver to ${result.updated} row(s) for race ${row.race_number}`, "ok");
        } catch (err) {
          cell.restoreOldValue();
          setSaveStatus(err.message || "Save failed", "err");
        }
        return;
      }

      if (!["race_number", "category", "query"].includes(field)) return;

      try {
        setSaveStatus("Saving...", "");
        await saveRowField(row.id, field, value);
        await reloadTableData();
        setSaveStatus("Saved", "ok");
      } catch (err) {
        cell.restoreOldValue();
        setSaveStatus(err.message || "Save failed", "err");
      }
    });

    table.on("dataFiltered", (filters, rows) => {
      document.getElementById("stat-filtered").textContent = rows.length;
      forceDarkTheme();
    });

    table.on("tableBuilt", forceDarkTheme);
    table.on("renderComplete", forceDarkTheme);

    document.getElementById("global-search").addEventListener("input", applyFilters);
    document.getElementById("race-filter").addEventListener("change", applyFilters);
    document.getElementById("category-filter").addEventListener("change", applyFilters);
    document.getElementById("freq-min").addEventListener("input", applyFilters);
    document.getElementById("unanswered-only").addEventListener("change", applyFilters);
    document.getElementById("add-row").addEventListener("click", openAddRowModal);
    document.getElementById("toggle-sn15-stats").addEventListener("click", async () => {
      const panel = document.getElementById("sn15-stats-panel");
      const btn = document.getElementById("toggle-sn15-stats");
      const willOpen = !panel.classList.contains("open");
      if (willOpen && panel.dataset.loaded !== "1") {
        btn.disabled = true;
        try {
          await loadSubnetRegistrationStats();
        } catch (err) {
          alert(err.message || "Failed to load SN15 stats");
          return;
        } finally {
          btn.disabled = false;
        }
      }
      panel.classList.toggle("open", willOpen);
      btn.classList.toggle("active", willOpen);
      if (!willOpen) setSn15Fullscreen(false);
      setTableHeight();
    });
    document.getElementById("sn15-refresh-btn").addEventListener("click", async () => {
      const btn = document.getElementById("sn15-refresh-btn");
      btn.disabled = true;
      try {
        await loadSubnetRegistrationStats(true, false);
      } catch (err) {
        alert(err.message || "Failed to refresh SN15 stats");
      } finally {
        btn.disabled = false;
      }
    });
    document.getElementById("sn15-refresh-times-btn").addEventListener("click", async () => {
      const btn = document.getElementById("sn15-refresh-times-btn");
      btn.disabled = true;
      try {
        await loadSubnetRegistrationStats(true, true);
      } catch (err) {
        alert(err.message || "Failed to refresh SN15 stats with registration times");
      } finally {
        btn.disabled = false;
      }
    });
    document.getElementById("sn15-fullscreen-btn").addEventListener("click", () => {
      const panel = document.getElementById("sn15-stats-panel");
      setSn15Fullscreen(!panel.classList.contains("fullscreen"));
    });
    document.getElementById("sn15-close-btn").addEventListener("click", closeSn15Stats);
    document.getElementById("sn15-perspective-tabs").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-perspective]");
      if (!btn || !window.subnetRegistrationStats) return;
      window.sn15Perspective = btn.dataset.perspective;
      document.querySelectorAll("#sn15-perspective-tabs button").forEach((el) => {
        el.classList.toggle("active", el === btn);
      });
      const rows = getSn15PerspectiveRows(window.subnetRegistrationStats, window.sn15Perspective);
      renderSn15ColdkeyTable(rows);
    });
    document.getElementById("toggle-dup-stats").addEventListener("click", async () => {
      const panel = document.getElementById("dup-stats-panel");
      const btn = document.getElementById("toggle-dup-stats");
      const willOpen = !panel.classList.contains("open");
      if (willOpen && panel.dataset.loaded !== "1") {
        await loadRaceWinners();
        await loadColdkeyMeta();
        await loadTransferPairs();
        await loadDuplicateStats();
        panel.dataset.loaded = "1";
      }
      panel.classList.toggle("open", willOpen);
      btn.classList.toggle("active", willOpen);
      if (!willOpen) setDupFullscreen(false);
      setTableHeight();
    });
    document.getElementById("dup-fullscreen-btn").addEventListener("click", () => {
      const panel = document.getElementById("dup-stats-panel");
      setDupFullscreen(!panel.classList.contains("fullscreen"));
    });
    document.getElementById("dup-close-btn").addEventListener("click", closeDupStats);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const dupPanel = document.getElementById("dup-stats-panel");
        const sn15Panel = document.getElementById("sn15-stats-panel");
        if (sn15Panel.classList.contains("fullscreen")) {
          setSn15Fullscreen(false);
        } else if (sn15Panel.classList.contains("open")) {
          closeSn15Stats();
        } else if (dupPanel.classList.contains("fullscreen")) {
          setDupFullscreen(false);
        } else if (dupPanel.classList.contains("open")) {
          closeDupStats();
        }
      }
    });
    document.getElementById("add-row-cancel").addEventListener("click", closeAddRowModal);
    document.getElementById("add-row-modal").addEventListener("click", (event) => {
      if (event.target.id === "add-row-modal") closeAddRowModal();
    });
    document.getElementById("add-row-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const errorEl = document.getElementById("add-row-error");
      const saveBtn = document.getElementById("add-row-save");
      const raceNumber = Number(document.getElementById("add-race-number").value);
      const category = document.getElementById("add-category").value;
      const query = document.getElementById("add-query").value.trim();
      const correctAnswer = document.getElementById("add-answer").value;

      if (raceNumber === null || raceNumber === undefined || raceNumber < 0) {
        errorEl.textContent = "Race number must be 0 (qualifying) or a positive integer.";
        return;
      }
      if (!category) {
        errorEl.textContent = "Category is required.";
        return;
      }
      if (!query) {
        errorEl.textContent = "Query is required.";
        return;
      }

      saveBtn.disabled = true;
      errorEl.textContent = "";
      try {
        const res = await fetch("/api/row", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            race_number: raceNumber,
            category,
            query,
            correct_answer: correctAnswer,
          }),
        });
        const result = await res.json();
        if (!res.ok) {
          errorEl.textContent = result.error || "Failed to add row";
          return;
        }
        await reloadTableData();
        closeAddRowModal();
        setSaveStatus(`Added row #${result.row.id}`, "ok");
      } catch (err) {
        errorEl.textContent = "Failed to add row";
      } finally {
        saveBtn.disabled = false;
      }
    });

    document.getElementById("export-csv").addEventListener("click", () => table.download("csv", "race-problems-filtered.csv"));
    document.getElementById("reset-filters").addEventListener("click", () => {
      document.getElementById("global-search").value = "";
      document.getElementById("race-filter").value = "";
      document.getElementById("category-filter").value = "";
      document.getElementById("freq-min").value = "1";
      table.clearFilter(true);
    });

    fetch("/api/data")
      .then((r) => r.json())
      .then(async (data) => {
        table.setData(data);
        updateStats(data);
        document.getElementById("stat-filtered").textContent = data.length;

        updateRaceFilterOptions(data);
        await loadRaceWinners();
        await loadColdkeyMeta();
        forceDarkTheme();
      });
  </script>
</body>
</html>
"""


def read_csv_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv_rows(csv_path: Path, rows: list[dict]) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sync_answers_in_rows(rows: list[dict]) -> int:
    """Apply one shared answer and solver per race+query (never across races)."""
    answers_by_race_query: dict[tuple[int, str], str] = {}
    agents_by_race_query: dict[tuple[int, str], str] = {}
    for row in rows:
        key = (int(row["race_number"]), normalize_query(row["query"]))
        answer = (row.get("correct_answer") or "").strip()
        agent = (row.get("answer_agent") or "").strip()
        if answer and key not in answers_by_race_query:
            answers_by_race_query[key] = answer
        if agent and key not in agents_by_race_query:
            agents_by_race_query[key] = agent

    changed = 0
    for row in rows:
        key = (int(row["race_number"]), normalize_query(row["query"]))
        shared_answer = answers_by_race_query.get(key, (row.get("correct_answer") or "").strip())
        shared_agent = agents_by_race_query.get(key, (row.get("answer_agent") or "").strip())
        if (row.get("correct_answer") or "") != shared_answer:
            row["correct_answer"] = shared_answer
            changed += 1
        if (row.get("answer_agent") or "") != shared_agent:
            row["answer_agent"] = shared_agent
            changed += 1
    return changed


def load_rows(csv_path: Path) -> list[dict]:
    from collections import defaultdict

    raw_rows = read_csv_rows(csv_path)
    for row in raw_rows:
        row["query"] = normalize_query(row["query"])
    sync_answers_in_rows(raw_rows)

    query_races: dict[str, set[int]] = defaultdict(set)
    for row in raw_rows:
        query_races[row["query"].strip()].add(int(row["race_number"]))

    rows = []
    for row in raw_rows:
        query = row["query"].strip()
        race_number = int(row["race_number"])
        other_races = sorted(query_races[query] - {race_number})
        rows.append(
            {
                "id": int(row.get("id") or 0),
                "race_number": race_number,
                "category": row["category"],
                "query": row["query"],
                "query_code": row.get("query_code") or encode_query(query),
                "frequency": int(row.get("frequency") or 0),
                "appeared_race_numbers": ", ".join(str(r) for r in other_races),
                "correct_answer": row.get("correct_answer") or "",
                "answer_agent": row.get("answer_agent") or "",
            }
        )
    return rows


def compute_race_duplicate_stats(rows: list[dict]) -> dict:
    """Per-race share of problems that already appeared in an earlier race."""
    from collections import defaultdict

    first_seen: dict[str, int] = {}
    by_race: dict[int, list[dict]] = defaultdict(list)

    for row in rows:
        query = row["query"].strip()
        race_number = int(row["race_number"])
        by_race[race_number].append(row)
        if query not in first_seen:
            first_seen[query] = race_number

    race_stats = []
    for race_number in sorted(by_race):
        race_rows = by_race[race_number]
        total = len(race_rows)
        dup_rows = [row for row in race_rows if first_seen[row["query"].strip()] < race_number]
        duplicates = len(dup_rows)
        duplicate_pct = round(100 * duplicates / total, 1) if total else 0.0
        answered = sum(
            1 for row in race_rows if (row.get("correct_answer") or "").strip()
        )
        answered_pct = round(100 * answered / total, 1) if total else 0.0
        known_duplicates = sum(
            1 for row in dup_rows if (row.get("correct_answer") or "").strip()
        )
        known_answer_pct = (
            round(100 * known_duplicates / duplicates, 1) if duplicates else 0.0
        )
        known_total_pct = round(100 * known_duplicates / total, 1) if total else 0.0
        race_stats.append(
            {
                "race_number": race_number,
                "total": total,
                "duplicates": duplicates,
                "new": total - duplicates,
                "duplicate_pct": duplicate_pct,
                "answered": answered,
                "answered_pct": answered_pct,
                "known_duplicates": known_duplicates,
                "known_answer_pct": known_answer_pct,
                "known_total_pct": known_total_pct,
            }
        )

    avg_duplicate_pct = (
        round(sum(item["duplicate_pct"] for item in race_stats) / len(race_stats), 1)
        if race_stats
        else 0.0
    )

    return {
        "races": race_stats,
        "summary": {
            "race_count": len(race_stats),
            "avg_duplicate_pct": avg_duplicate_pct,
            "total_rows": sum(item["total"] for item in race_stats),
            "total_duplicates": sum(item["duplicates"] for item in race_stats),
        },
    }


def next_row_id(rows: list[dict]) -> int:
    return max((int(row.get("id") or 0) for row in rows), default=0) + 1


def sync_query_frequency(rows: list[dict], query: str) -> None:
    target = normalize_query(query)
    matching = [row for row in rows if normalize_query(row["query"]) == target]
    frequency = str(len(matching))
    for row in matching:
        row["frequency"] = frequency


def sync_appeared_race_numbers(rows: list[dict]) -> None:
    from collections import defaultdict

    query_races: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        query_races[normalize_query(row["query"])].add(int(row["race_number"]))

    for row in rows:
        query = normalize_query(row["query"])
        race_number = int(row["race_number"])
        other_races = sorted(query_races[query] - {race_number})
        row["appeared_race_numbers"] = ", ".join(str(r) for r in other_races)


def find_csv_row_by_id(rows: list[dict], row_id: int) -> dict | None:
    for row in rows:
        if int(row.get("id") or 0) == row_id:
            return row
    return None


def update_row(
    csv_path: Path,
    row_id: int,
    *,
    race_number: int | None = None,
    category: str | None = None,
    query: str | None = None,
) -> dict:
    rows = read_csv_rows(csv_path)
    target_row = find_csv_row_by_id(rows, row_id)
    if not target_row:
        raise ValueError("row not found")

    old_query = normalize_query(target_row["query"])

    if category is not None:
        category = category.strip()
        if category not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}")
        target_row["category"] = category

    if query is not None:
        query = normalize_query(query)
        if not query:
            raise ValueError("query is required")
        target_row["query"] = query
        target_row["query_code"] = encode_query(query)

    if race_number is not None:
        if race_number < 0:
            raise ValueError("race_number must be 0 (qualifying) or a positive integer")
        target_row["race_number"] = str(race_number)

    new_race = int(target_row["race_number"])
    new_query = normalize_query(target_row["query"])
    for row in rows:
        if int(row.get("id") or 0) == row_id:
            continue
        if int(row["race_number"]) == new_race and normalize_query(row["query"]) == new_query:
            raise ValueError("A row with this race number and query already exists")

    if query is not None and new_query != old_query:
        sync_query_frequency(rows, old_query)
        sync_query_frequency(rows, new_query)
        sync_answers_in_rows(rows)
    elif race_number is not None or query is not None:
        sync_query_frequency(rows, new_query)

    sync_appeared_race_numbers(rows)
    write_csv_rows(csv_path, rows)
    return next(row for row in load_rows(csv_path) if row["id"] == row_id)


def add_row(
    csv_path: Path,
    race_number: int,
    category: str,
    query: str,
    correct_answer: str = "",
) -> dict:
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    rows = read_csv_rows(csv_path)
    query = normalize_query(query)
    if not query:
        raise ValueError("query is required")

    for row in rows:
        if int(row["race_number"]) == race_number and normalize_query(row["query"]) == query:
            raise ValueError("A row with this race number and query already exists")

    shared_answer = correct_answer.strip()
    shared_agent = ""
    if not shared_answer:
        for row in rows:
            if (
                int(row["race_number"]) == race_number
                and normalize_query(row["query"]) == query
            ):
                existing = (row.get("correct_answer") or "").strip()
                if existing:
                    shared_answer = existing
                    shared_agent = (row.get("answer_agent") or "").strip()
                    break
    else:
        for row in rows:
            if (
                int(row["race_number"]) == race_number
                and normalize_query(row["query"]) == query
            ):
                existing_agent = (row.get("answer_agent") or "").strip()
                if existing_agent:
                    shared_agent = existing_agent
                    break

    new_row = {
        "id": str(next_row_id(rows)),
        "race_number": str(race_number),
        "category": category,
        "query": query,
        "query_code": encode_query(query),
        "frequency": "1",
        "appeared_race_numbers": "",
        "correct_answer": shared_answer,
        "answer_agent": shared_agent,
    }
    rows.append(new_row)
    sync_query_frequency(rows, query)

    if correct_answer.strip():
        for row in rows:
            if int(row["race_number"]) == race_number and normalize_query(row["query"]) == query:
                row["correct_answer"] = correct_answer

    sync_appeared_race_numbers(rows)
    write_csv_rows(csv_path, rows)
    row_id = int(new_row["id"])
    return next(row for row in load_rows(csv_path) if row["id"] == row_id)


def save_answer(
    csv_path: Path,
    query: str,
    correct_answer: str,
    race_number: int | None = None,
) -> int:
    rows = read_csv_rows(csv_path)
    target = normalize_query(query)
    updated = 0
    for row in rows:
        if normalize_query(row["query"]) != target:
            continue
        if race_number is not None and int(row["race_number"]) != race_number:
            continue
        row["correct_answer"] = correct_answer
        if not correct_answer.strip():
            row["answer_agent"] = ""
        updated += 1
    if updated:
        sync_answers_in_rows(rows)
        write_csv_rows(csv_path, rows)
    return updated


def save_answer_agent(
    csv_path: Path,
    query: str,
    answer_agent: str,
    race_number: int | None = None,
) -> int:
    rows = read_csv_rows(csv_path)
    target = normalize_query(query)
    updated = 0
    for row in rows:
        if normalize_query(row["query"]) != target:
            continue
        if race_number is not None and int(row["race_number"]) != race_number:
            continue
        row["answer_agent"] = answer_agent
        updated += 1
    if updated:
        sync_answers_in_rows(rows)
        write_csv_rows(csv_path, rows)
    return updated


@app.after_request
def disable_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/")
def index():
    csv_path = app.config["CSV_PATH"]
    return render_template_string(
        HTML,
        csv_name=csv_path.name,
    )


@app.route("/api/data")
def api_data():
    rows = load_rows(app.config["CSV_PATH"])
    return jsonify(rows)


@app.route("/api/coldkey-transfers")
def api_coldkey_transfers():
    db_path = app.config["RACE_DB_PATH"]
    pairs = [serialize_transfer_pair(item) for item in get_coldkey_transfer_pairs(db_path)]
    by_coldkey, winner_pairs = build_transfer_indexes(pairs)
    winner_coldkeys = sorted(get_stored_coldkeys(db_path))
    serialized_by_coldkey = {
        coldkey: [serialize_transfer_pair(item) for item in rows]
        for coldkey, rows in by_coldkey.items()
    }
    source = "taostats" if os.getenv("TAOSTATS_API_KEY") else "recent_blocks"
    return jsonify(
        {
            "pairs": pairs,
            "by_coldkey": serialized_by_coldkey,
            "winner_pairs": winner_pairs,
            "winner_coldkeys": winner_coldkeys,
            "pair_count": len(pairs),
            "winner_pair_count": len(winner_pairs),
            "cached_coldkeys": len(winner_coldkeys),
            "source": source,
            "source_note": (
                "Full transfer history via Taostats API"
                if os.getenv("TAOSTATS_API_KEY")
                else "Recent on-chain blocks only (set TAOSTATS_API_KEY for full history)"
            ),
        }
    )


@app.route("/api/coldkey-meta")
def api_coldkey_meta():
    rows = [serialize_coldkey_meta(item) for item in get_all_coldkey_meta(app.config["RACE_DB_PATH"])]
    return jsonify(rows)


@app.route("/api/race-winners")
def api_race_winners():
    return jsonify(get_all_winners(app.config["RACE_DB_PATH"]))


@app.route("/api/sync-winners", methods=["POST"])
def api_sync_winners():
    result = sync_race_winners(app.config["RACE_DB_PATH"])
    return jsonify({"ok": True, **result})


@app.route("/api/subnet-registrations")
def api_subnet_registrations():
    include_times = request.args.get("registration_times", "").lower() in {"1", "true", "yes"}
    include_costs_param = request.args.get("registration_costs", "").lower()
    include_costs = None
    if include_costs_param in {"1", "true", "yes"}:
        include_costs = True
    elif include_costs_param in {"0", "false", "no"}:
        include_costs = False
    try:
        return jsonify(
            fetch_subnet_registration_stats(
                include_registration_times=include_times,
                include_registration_costs=include_costs,
            )
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/duplicate-stats")
def api_duplicate_stats():
    rows = load_rows(app.config["CSV_PATH"])
    return jsonify(compute_race_duplicate_stats(rows))


@app.route("/api/row", methods=["POST"])
def api_add_row():
    payload = request.get_json(silent=True) or {}
    try:
        race_number = int(payload.get("race_number", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "race_number must be a positive integer"}), 400

    category = str(payload.get("category", "")).strip()
    query = str(payload.get("query", "")).strip()
    correct_answer = str(payload.get("correct_answer", ""))

    if race_number < 0:
        return jsonify({"error": "race_number must be 0 (qualifying) or a positive integer"}), 400
    if not category:
        return jsonify({"error": "category is required"}), 400
    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        row = add_row(app.config["CSV_PATH"], race_number, category, query, correct_answer)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "row": row})


@app.route("/api/row/update", methods=["POST"])
def api_update_row():
    payload = request.get_json(silent=True) or {}
    try:
        row_id = int(payload.get("id", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "id must be a positive integer"}), 400

    if row_id < 1:
        return jsonify({"error": "id must be a positive integer"}), 400

    race_number = payload.get("race_number")
    category = payload.get("category")
    query = payload.get("query")

    if race_number is None and category is None and query is None:
        return jsonify({"error": "no fields to update"}), 400

    kwargs: dict = {}
    if race_number is not None:
        try:
            kwargs["race_number"] = int(race_number)
        except (TypeError, ValueError):
            return jsonify({"error": "race_number must be a positive integer"}), 400
    if category is not None:
        kwargs["category"] = str(category).strip()
    if query is not None:
        kwargs["query"] = str(query)

    try:
        row = update_row(app.config["CSV_PATH"], row_id, **kwargs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "row": row})


@app.route("/api/answer", methods=["POST"])
def api_save_answer():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query", "")).strip()
    correct_answer = str(payload.get("correct_answer", ""))
    race_number = payload.get("race_number")
    if not query:
        return jsonify({"error": "query is required"}), 400

    parsed_race: int | None = None
    if race_number is not None:
        try:
            parsed_race = int(race_number)
        except (TypeError, ValueError):
            return jsonify({"error": "race_number must be an integer"}), 400

    updated = save_answer(app.config["CSV_PATH"], query, correct_answer, parsed_race)
    if not updated:
        return jsonify({"error": "query not found"}), 404

    return jsonify({"ok": True, "updated": updated, "query": query})


@app.route("/api/answer-agent", methods=["POST"])
def api_save_answer_agent():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query", "")).strip()
    answer_agent = str(payload.get("answer_agent", ""))
    race_number = payload.get("race_number")
    if not query:
        return jsonify({"error": "query is required"}), 400

    parsed_race: int | None = None
    if race_number is not None:
        try:
            parsed_race = int(race_number)
        except (TypeError, ValueError):
            return jsonify({"error": "race_number must be an integer"}), 400

    updated = save_answer_agent(app.config["CSV_PATH"], query, answer_agent, parsed_race)
    if not updated:
        return jsonify({"error": "query not found"}), 404

    return jsonify({"ok": True, "updated": updated, "query": query})


def startup_sync_winners() -> None:
    db_path = app.config["RACE_DB_PATH"]
    init_db(db_path)
    try:
        result = sync_race_winners(db_path)
        if result["fetched"]:
            print(
                f"Cached {result['fetched']} new race winners "
                f"({result['total_stored']} total in DB)"
            )
        else:
            print(f"Race winners DB up to date ({result['total_stored']} races cached)")
        if result["errors"]:
            print(f"Winner sync warnings: {len(result['errors'])}")
    except Exception as exc:
        print(f"Winner sync failed: {exc}")

    try:
        meta_result = sync_coldkey_meta(db_path)
        if meta_result["backfilled_coldkeys"]:
            print(f"Backfilled coldkeys on {meta_result['backfilled_coldkeys']} race winners")
        if meta_result["fetched"]:
            print(
                f"Cached {meta_result['fetched']} new coldkey profiles "
                f"({meta_result['total_cached']} total in DB)"
            )
        else:
            print(f"Coldkey meta DB up to date ({meta_result['total_cached']} coldkeys cached)")
        if meta_result["errors"]:
            print(f"Coldkey meta warnings: {len(meta_result['errors'])}")
    except Exception as exc:
        print(f"Coldkey meta sync failed: {exc}")

    try:
        transfer_result = sync_coldkey_transfer_pairs(db_path)
        if transfer_result["fetched"]:
            print(
                f"Cached transfer pairs for {transfer_result['fetched']} coldkeys "
                f"via {transfer_result['source']}"
            )
        else:
            print(
                f"Transfer pair DB up to date ({transfer_result['already_cached']} coldkeys cached)"
            )
        if transfer_result["errors"]:
            print(f"Transfer pair warnings: {len(transfer_result['errors'])}")
        if not os.getenv("TAOSTATS_API_KEY"):
            print("Tip: set TAOSTATS_API_KEY for full transfer history instead of recent blocks only")
    except Exception as exc:
        print(f"Transfer pair sync failed: {exc}")


def open_browser(url: str) -> None:
    webbrowser.open(url)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="View race problems queries in an Excel-like grid.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to CSV file (default: {DEFAULT_CSV.name})",
    )
    parser.add_argument("--port", type=int, default=5050, help="Port for the local web server")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind (default: 0.0.0.0 for remote access; use 127.0.0.1 for local only)",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    app.config["CSV_PATH"] = args.csv.resolve()
    app.config["RACE_DB_PATH"] = Path(__file__).resolve().parent / "race_winners.db"

    rows = read_csv_rows(app.config["CSV_PATH"])
    changed = False
    for row in rows:
        cleaned = normalize_query(row["query"])
        if row["query"] != cleaned:
            row["query"] = cleaned
            changed = True
    if sync_answers_in_rows(rows):
        changed = True
    if changed:
        write_csv_rows(app.config["CSV_PATH"], rows)
        print("Normalized query line breaks in CSV")

    url = f"http://127.0.0.1:{args.port}"
    public_url = f"http://{args.host}:{args.port}" if args.host == "0.0.0.0" else url

    print(f"Loading: {app.config['CSV_PATH']}")
    if args.host == "0.0.0.0":
        print(f"Listening on all interfaces — open http://<vps-ip>:{args.port} from your machine")
    print(f"Open {url} in your browser")

    if not args.no_browser:
        Timer(0.8, open_browser, args=[url]).start()

    Timer(0.1, startup_sync_winners).start()

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
