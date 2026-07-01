#!/usr/bin/env python3
"""Web UI for analyzing race query rewards: correct vs failed product comparisons."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path
from threading import Timer

from flask import Flask, jsonify, render_template_string, request

from product_catalog import DEFAULT_CATALOG, fetch_product_raw, parse_product_ids
from race_reward_analysis_db import (
    DEFAULT_DB,
    get_analysis,
    get_summary,
    init_db,
    list_analysis,
    update_analysis_notes,
)

ORO_RACE = Path(__file__).resolve().parent
STATIC_DIR = ORO_RACE / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Race Reward Analysis</title>
  <link href="/static/tabulator_midnight.min.css?v=1" rel="stylesheet">
  <script src="/static/tabulator.min.js?v=1"></script>
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
      --good: #22c55e;
      --bad: #ef4444;
      --warn: #f59e0b;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      height: 100vh;
      overflow: hidden;
      font-family: "Segoe UI", Calibri, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .layout {
      display: grid;
      grid-template-columns: 280px 1fr 420px;
      grid-template-rows: auto 1fr;
      height: 100vh;
    }
    header {
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      padding: 12px 16px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
    }
    header h1 { margin: 0; font-size: 1.1rem; margin-right: auto; }
    header .stat { font-size: 0.82rem; color: var(--muted); }
    header .stat b { color: var(--text); }
    .sidebar, .detail {
      overflow: auto;
      background: var(--panel);
      border-right: 1px solid var(--border);
      padding: 12px;
    }
    .detail { border-right: none; border-left: 1px solid var(--border); }
    .main { overflow: hidden; display: flex; flex-direction: column; background: var(--bg); }
    #table-wrap {
      flex: 1;
      min-height: 0;
      margin: 10px 12px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      background: var(--panel);
      box-shadow: 0 4px 24px rgba(0, 0, 0, 0.35);
    }
    /* Tabulator polish */
    .tabulator {
      background: transparent !important;
      border: none !important;
      font-size: 0.84rem;
    }
    .tabulator .tabulator-header {
      background: linear-gradient(180deg, #1f1f1f 0%, #181818 100%) !important;
      border-bottom: 1px solid #333 !important;
    }
    .tabulator .tabulator-header .tabulator-col {
      background: transparent !important;
      border-right: 1px solid #2a2a2a !important;
    }
    .tabulator .tabulator-header .tabulator-col-content {
      padding: 10px 8px !important;
    }
    .tabulator .tabulator-header .tabulator-col-title {
      font-weight: 600;
      font-size: 0.75rem;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .tabulator .tabulator-tableholder .tabulator-table .tabulator-row {
      background: #141414 !important;
      border-bottom: 1px solid #222 !important;
      min-height: 38px;
      transition: background 0.12s ease, box-shadow 0.12s ease;
    }
    .tabulator .tabulator-tableholder .tabulator-table .tabulator-row:nth-child(even):not([data-selected="true"]) {
      background: #121212 !important;
    }
    .tabulator .tabulator-tableholder .tabulator-table .tabulator-row:not([data-selected="true"]):hover {
      background: #1e2a3d !important;
      cursor: pointer;
    }
    .tabulator .tabulator-tableholder .tabulator-table .tabulator-row[data-selected="true"] {
      background: rgba(59, 130, 246, 0.25) !important;
      box-shadow: inset 3px 0 0 #3b82f6 !important;
    }
    .tabulator .tabulator-tableholder .tabulator-table .tabulator-row[data-selected="true"]:hover {
      background: rgba(59, 130, 246, 0.32) !important;
    }
    .tabulator .tabulator-cell {
      border-right: 1px solid #1f1f1f !important;
      padding: 9px 8px !important;
      line-height: 1.35;
    }
    .tabulator .tabulator-placeholder {
      color: var(--muted);
      padding: 32px;
    }
    .cell-race {
      display: inline-block;
      min-width: 2rem;
      padding: 2px 8px;
      border-radius: 6px;
      background: rgba(59, 130, 246, 0.15);
      color: #93c5fd;
      font-weight: 600;
      font-size: 0.78rem;
    }
    .cell-code {
      font-family: ui-monospace, "Cascadia Code", monospace;
      font-size: 0.78rem;
      color: #cbd5e1;
    }
    .cell-cat {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 500;
    }
    .cell-cat.product { background: rgba(34, 197, 94, 0.15); color: #86efac; }
    .cell-cat.shop { background: rgba(245, 158, 11, 0.15); color: #fcd34d; }
    .cell-cat.voucher { background: rgba(168, 85, 247, 0.15); color: #d8b4fe; }
    .cell-fails {
      display: inline-block;
      min-width: 1.4rem;
      padding: 2px 7px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.78rem;
      text-align: center;
    }
    .cell-fails.has { background: rgba(239, 68, 68, 0.18); color: #fca5a5; }
    .cell-fails.none { background: rgba(107, 114, 128, 0.2); color: var(--muted); }
    .cell-ids {
      font-family: ui-monospace, "Cascadia Code", monospace;
      font-size: 0.76rem;
      color: #94a3b8;
    }
    .cell-query { color: #e2e8f0; }
    .cell-notes {
      color: #fde68a;
      font-size: 0.78rem;
      font-style: italic;
    }
    .cell-notes.empty { color: #4b5563; font-style: normal; }
    label { display: block; font-size: 0.78rem; color: var(--muted); margin-bottom: 4px; }
    input, select, textarea, button {
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 7px 10px;
      font-size: 0.85rem;
    }
    button { cursor: pointer; }
    button:hover { border-color: var(--accent); }
    .field { margin-bottom: 12px; }
    .field input, .field select { width: 100%; }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.72rem;
      background: var(--panel-2);
      border: 1px solid var(--border);
    }
    .pill.good { border-color: var(--good); color: var(--good); }
    .pill.bad { border-color: var(--bad); color: var(--bad); }
    .query-box {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      font-size: 0.82rem;
      line-height: 1.45;
      white-space: pre-wrap;
      max-height: 160px;
      overflow: auto;
    }
    .product-card {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 10px;
      font-size: 0.78rem;
    }
    .product-card.correct { border-color: rgba(34,197,94,0.45); }
    .product-card.failed { border-color: rgba(239,68,68,0.45); }
    .product-card h4 {
      margin: 0 0 6px;
      font-size: 0.82rem;
      color: var(--text);
    }
    .meta { color: var(--muted); margin-bottom: 6px; }
    .attrs { margin-top: 6px; }
    .attr-row { display: flex; gap: 6px; margin-bottom: 3px; }
    .attr-key { color: var(--muted); min-width: 110px; }
    .attr-val { flex: 1; }
    .diff-missing { color: var(--bad); }
    .diff-extra { color: var(--warn); }
    .diff-match { color: var(--good); }
    .section-title {
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      margin: 14px 0 8px;
    }
    .empty { color: var(--muted); font-size: 0.85rem; padding: 20px; text-align: center; }
    .failed-set-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .failed-set-tabs button.active { border-color: var(--accent); color: var(--accent); }
    textarea.notes {
      width: 100%;
      min-height: 90px;
      resize: vertical;
      font-family: inherit;
    }
    .compare-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .loading { color: var(--muted); font-size: 0.82rem; padding: 8px 0; }
    @media (max-width: 1200px) {
      .layout { grid-template-columns: 240px 1fr; }
      .detail { display: none; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <header>
      <h1>Race Reward Analysis</h1>
      <span class="stat">Queries: <b id="stat-total">-</b></span>
      <span class="stat">With failures: <b id="stat-failures">-</b></span>
      <span class="stat">Races: <b id="stat-races">-</b></span>
      <button id="btn-refresh">Refresh</button>
    </header>

    <aside class="sidebar">
      <div class="field">
        <label>Search</label>
        <input id="filter-search" placeholder="query, product id, code...">
      </div>
      <div class="field">
        <label>Race number</label>
        <select id="filter-race"><option value="">All races</option></select>
      </div>
      <div class="field">
        <label>Category</label>
        <select id="filter-category">
          <option value="">All</option>
          <option>Product</option>
          <option>Shop</option>
          <option>Voucher</option>
        </select>
      </div>
      <div class="field">
        <label>Failures</label>
        <select id="filter-failures">
          <option value="">All</option>
          <option value="yes">Has typical failures</option>
          <option value="no">No failures recorded</option>
        </select>
      </div>
      <p style="font-size:0.75rem;color:var(--muted);margin-top:20px;line-height:1.4;">
        Click a row to load product details on demand and compare correct vs failed recommendations.
      </p>
    </aside>

    <section class="main">
      <div id="table-wrap"></div>
    </section>

    <aside class="detail" id="detail-panel">
      <div class="empty">Select a query to analyze</div>
    </aside>
  </div>

<script>
let table = null;
let rows = [];
let selectedId = null;
let selectedDetail = null;
let activeFailedIdx = 0;
let loadedProducts = { correct: [], failed: {} };
let productsLoading = false;

async function loadSummary() {
  const res = await fetch("/api/summary");
  const data = await res.json();
  document.getElementById("stat-total").textContent = data.total_queries;
  document.getElementById("stat-failures").textContent = data.queries_with_failures;
  document.getElementById("stat-races").textContent = data.race_count;
}

function currentFilters() {
  const params = new URLSearchParams();
  const search = document.getElementById("filter-search").value.trim();
  const race = document.getElementById("filter-race").value;
  const category = document.getElementById("filter-category").value;
  const failures = document.getElementById("filter-failures").value;
  if (search) params.set("search", search);
  if (race) params.set("race_number", race);
  if (category) params.set("category", category);
  if (failures === "yes") params.set("has_failures", "1");
  if (failures === "no") params.set("has_failures", "0");
  return params;
}

async function loadRows() {
  const params = currentFilters();
  const res = await fetch("/api/rows?" + params.toString());
  rows = await res.json();
  if (!table) initTable();
  table.replaceData(rows);
  populateRaceFilter(rows);
}

function populateRaceFilter(data) {
  const select = document.getElementById("filter-race");
  const current = select.value;
  const races = [...new Set(data.map(r => r.race_number))].sort((a, b) => b - a);
  select.innerHTML = '<option value="">All races</option>' +
    races.map(r => `<option value="${r}">Race ${r}</option>`).join("");
  if (current) select.value = current;
}

async function loadRows() {
  const params = currentFilters();
  const res = await fetch("/api/rows?" + params.toString());
  rows = await res.json();
  if (!table) initTable();
  table.replaceData(rows);
  populateRaceFilter(rows);
  applyRowHighlight();
}

function applyRowHighlight() {
  if (!table) return;
  const wrap = document.getElementById("table-wrap");
  wrap.querySelectorAll(".tabulator-row").forEach(el => {
    el.removeAttribute("data-selected");
    el.classList.remove("row-selected-custom");
  });
  if (selectedId == null) return;
  table.getRows().forEach(r => {
    if (Number(r.getData().id) === Number(selectedId)) {
      const el = r.getElement();
      el.setAttribute("data-selected", "true");
    }
  });
}

function catClass(cat) {
  const c = (cat || "").toLowerCase();
  if (c === "product") return "product";
  if (c === "shop") return "shop";
  if (c === "voucher") return "voucher";
  return "";
}

function initTable() {
  table = new Tabulator("#table-wrap", {
    data: rows,
    layout: "fitColumns",
    height: "100%",
    selectable: false,
    placeholder: "No matching queries",
    rowFormatter: function(row) {
      const el = row.getElement();
      const isSelected = Number(row.getData().id) === Number(selectedId);
      if (isSelected) el.setAttribute("data-selected", "true");
      else el.removeAttribute("data-selected");
    },
    columns: [
      { title: "Race", field: "race_number", width: 72, hozAlign: "center",
        formatter: c => `<span class="cell-race">${c.getValue()}</span>` },
      { title: "Code", field: "query_code", width: 72, hozAlign: "center",
        formatter: c => `<span class="cell-code">${esc(c.getValue() || "")}</span>` },
      { title: "Cat", field: "category", width: 88, hozAlign: "center",
        formatter: c => {
          const v = c.getValue() || "";
          return `<span class="cell-cat ${catClass(v)}">${esc(v)}</span>`;
        }},
      { title: "Correct IDs", field: "correct_product_ids", width: 130,
        formatter: c => {
          const v = c.getValue() || "";
          const s = v.length > 22 ? v.slice(0, 22) + "…" : v;
          return `<span class="cell-ids" title="${esc(v)}">${esc(s)}</span>`;
        }},
      { title: "Fails", field: "failed_count", width: 64, hozAlign: "center",
        formatter: c => {
          const n = c.getValue() || 0;
          const cls = n > 0 ? "has" : "none";
          return `<span class="cell-fails ${cls}">${n}</span>`;
        }},
      { title: "Top failed IDs", field: "failed_top_ids", width: 150,
        formatter: c => {
          const v = c.getValue() || "";
          if (!v) return '<span class="cell-ids">—</span>';
          const s = v.length > 28 ? v.slice(0, 28) + "…" : v;
          return `<span class="cell-ids" title="${esc(v)}">${esc(s)}</span>`;
        }},
      { title: "Query", field: "query", minWidth: 220,
        formatter: c => {
          const v = c.getValue() || "";
          const s = v.length > 80 ? v.slice(0, 80) + "…" : v;
          return `<span class="cell-query" title="${esc(v)}">${esc(s)}</span>`;
        }},
      { title: "Notes", field: "analysis_notes", minWidth: 160,
        formatter: c => {
          const v = (c.getValue() || "").trim();
          if (!v) return '<span class="cell-notes empty">—</span>';
          const s = v.length > 60 ? v.slice(0, 60) + "…" : v;
          return `<span class="cell-notes" title="${esc(v)}">${esc(s)}</span>`;
        }},
    ],
  });
  table.on("rowClick", (_, row) => {
    if (Number(row.getData().id) === Number(selectedId)) return;
    selectRow(row.getData().id);
  });
}

async function selectRow(id) {
  selectedId = Number(id);
  applyRowHighlight();
  activeFailedIdx = 0;
  loadedProducts = { correct: [], failed: {} };
  const res = await fetch(`/api/detail/${id}`);
  selectedDetail = await res.json();
  renderDetail();
  await loadProductsForDetail();
}

async function fetchProducts(productIds) {
  if (!productIds) return [];
  const res = await fetch(`/api/products?product_ids=${encodeURIComponent(productIds)}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.products || [];
}

async function loadProductsForDetail() {
  if (!selectedDetail) return;
  const d = selectedDetail;
  productsLoading = true;
  renderDetail();

  const correctIds = d.correct_product_ids || "";
  loadedProducts.correct = await fetchProducts(correctIds);

  const failedList = d.failed_products || [];
  for (const entry of failedList.slice(0, 3)) {
    const ids = entry.product_ids;
    if (ids && !loadedProducts.failed[ids]) {
      loadedProducts.failed[ids] = await fetchProducts(ids);
    }
  }

  productsLoading = false;
  renderDetail();
}

function flattenAttrs(obj) {
  const out = {};
  if (!obj || typeof obj !== "object") return out;
  for (const [k, v] of Object.entries(obj)) {
    if (Array.isArray(v)) out[k] = v.join(", ");
    else if (v && typeof v === "object") out[k] = JSON.stringify(v);
    else out[k] = String(v ?? "");
  }
  return out;
}

function productTitle(p) {
  if (!p) return "(unknown)";
  if (p.title) return String(p.title);
  return `Product ${p.product_id || "?"}`;
}

function formatSkuOptions(sku) {
  if (!sku || typeof sku !== "object") return "";
  return Object.entries(sku).map(([key, val]) => {
    if (val && typeof val === "object") {
      const parts = Object.entries(val).map(([k, v]) => `${k}: ${v}`);
      return `#${key} { ${parts.join(", ")} }`;
    }
    return `#${key}: ${val}`;
  }).join(" · ");
}

function renderProductCard(p, kind, compareAttrs) {
  const attrs = flattenAttrs(p.attributes || {});
  const skuText = formatSkuOptions(p.sku_options);
  let attrHtml = "";
  const keys = new Set([...Object.keys(attrs), ...(compareAttrs ? Object.keys(compareAttrs) : [])]);
  for (const key of [...keys].sort()) {
    const val = attrs[key] || "";
    const ref = compareAttrs ? (compareAttrs[key] || "") : null;
    let cls = "attr-val";
    if (compareAttrs && ref !== null) {
      if (!val && ref) cls += " diff-missing";
      else if (val && !ref) cls += " diff-extra";
      else if (val === ref) cls += " diff-match";
      else cls += " diff-missing";
    }
    attrHtml += `<div class="attr-row"><span class="attr-key">${esc(key)}</span><span class="${cls}">${esc(val || "—")}</span></div>`;
  }
  const serviceText = Array.isArray(p.service) ? p.service.join(", ") : (p.service || "");
  return `<div class="product-card ${kind}">
    <h4>${esc(productTitle(p))}</h4>
    <div class="meta">ID: ${esc(String(p.product_id || ""))}</div>
    <div class="meta">Price: ${esc(String(p.price ?? "—"))}</div>
    ${p.category ? `<div class="meta">Category: ${esc(p.category)}</div>` : ""}
    ${serviceText ? `<div class="meta">Service: ${esc(serviceText)}</div>` : ""}
    <div class="section-title" style="margin-top:8px">Attributes</div>
    <div class="attrs">${attrHtml || '<span class="meta">—</span>'}</div>
    ${skuText ? `<div class="section-title" style="margin-top:8px">SKU options</div><div class="meta">${esc(skuText)}</div>` : ""}
  </div>`;
}

function esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function mergedCorrectAttrs(products) {
  const merged = {};
  for (const p of products || []) {
    Object.assign(merged, flattenAttrs(p.attributes || {}));
  }
  return merged;
}

function renderDetail() {
  const panel = document.getElementById("detail-panel");
  if (!selectedDetail) {
    panel.innerHTML = '<div class="empty">Select a query to analyze</div>';
    return;
  }
  const d = selectedDetail;
  const correctProducts = loadedProducts.correct || [];
  const correctReward = d.correct_reward || [];
  const failedList = (d.failed_products || []).slice(0, 3);
  const correctAttrs = mergedCorrectAttrs(correctProducts.length ? correctProducts : correctReward);

  let failedTabs = "";
  if (failedList.length) {
    failedTabs = '<div class="failed-set-tabs">' +
      failedList.map((f, i) =>
        `<button class="${i===activeFailedIdx?'active':''}" data-idx="${i}">${esc(f.product_ids)} (${f.count||1}x)</button>`
      ).join("") + '</div>';
  }

  const activeFailed = failedList[activeFailedIdx];
  const failedProducts = activeFailed ? (loadedProducts.failed[activeFailed.product_ids] || []) : [];
  const failedAttrs = mergedCorrectAttrs(failedProducts);

  let correctHtml = "";
  if (productsLoading && !correctProducts.length) {
    correctHtml = '<div class="loading">Loading correct product info…</div>';
  } else if (correctProducts.length) {
    correctHtml = correctProducts.map(p => renderProductCard(p, "correct", failedAttrs)).join("");
  } else if (correctReward.length) {
    correctHtml = correctReward.map(p => renderProductCard(
      {
        product_id: p.product_id,
        title: Array.isArray(p.title) ? p.title.join(" ") : (p.title || ""),
        attributes: (p.attributes || [])[0] || {},
        price: null,
        sku_options: {},
        service: [],
        category: "",
      },
      "correct", failedAttrs
    )).join("");
  } else {
    correctHtml = `<div class="product-card correct"><div class="meta">IDs: ${esc(d.correct_product_ids)}</div></div>`;
  }

  let failedHtml = failedList.length
    ? (productsLoading && activeFailed && !failedProducts.length
      ? '<div class="loading">Loading failed product info…</div>'
      : failedProducts.length
        ? failedProducts.map(p => renderProductCard(p, "failed", correctAttrs)).join("")
        : `<div class="product-card failed"><div class="meta">IDs: ${esc(activeFailed?.product_ids || "")}</div></div>`)
    : '<div class="meta">No failed recommendations recorded for this query.</div>';

  panel.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
      <span class="pill">Race ${d.race_number}</span>
      <span class="pill">${esc(d.category||"")}</span>
      <span class="pill">${esc(d.query_code||"")}</span>
    </div>
    <div class="section-title">Query</div>
    <div class="query-box">${esc(d.query)}</div>
    <div class="section-title">Correct product IDs</div>
    <div class="meta" style="margin-bottom:8px">${esc(d.correct_product_ids)}</div>
    <div class="section-title">Correct (reward)</div>
    ${correctHtml}
    <div class="section-title">Typical failed recommendations</div>
    ${failedTabs}
    ${failedHtml}
    <div class="section-title">Analysis notes</div>
    <textarea class="notes" id="notes-input">${esc(d.analysis_notes||"")}</textarea>
    <div style="margin-top:8px;display:flex;gap:8px;">
      <button id="btn-save-notes">Save notes</button>
    </div>
  `;

  panel.querySelectorAll(".failed-set-tabs button").forEach(btn => {
    btn.addEventListener("click", async () => {
      activeFailedIdx = Number(btn.dataset.idx);
      renderDetail();
      const entry = failedList[activeFailedIdx];
      if (entry && !loadedProducts.failed[entry.product_ids]) {
        productsLoading = true;
        renderDetail();
        loadedProducts.failed[entry.product_ids] = await fetchProducts(entry.product_ids);
        productsLoading = false;
        renderDetail();
      }
    });
  });
  document.getElementById("btn-save-notes").addEventListener("click", saveNotes);
}

async function saveNotes() {
  const notes = document.getElementById("notes-input").value;
  await fetch(`/api/notes/${selectedId}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({notes}),
  });
  const row = table?.getRows().find(r => Number(r.getData().id) === Number(selectedId));
  if (row) row.update({ analysis_notes: notes });
  if (selectedDetail) selectedDetail.analysis_notes = notes;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

document.getElementById("btn-refresh").addEventListener("click", () => { loadSummary(); loadRows(); });
["filter-race","filter-category","filter-failures"].forEach(id => {
  document.getElementById(id).addEventListener("change", loadRows);
});
document.getElementById("filter-search").addEventListener("input", debounce(loadRows, 250));

loadSummary();
loadRows();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/summary")
def api_summary():
    return jsonify(get_summary(app.config["DB_PATH"]))


@app.route("/api/rows")
def api_rows():
    race_raw = request.args.get("race_number")
    race_number = int(race_raw) if race_raw else None
    category = request.args.get("category") or None
    search = request.args.get("search") or None
    has_failures_raw = request.args.get("has_failures")
    has_failures = None
    if has_failures_raw == "1":
        has_failures = True
    elif has_failures_raw == "0":
        has_failures = False
    return jsonify(
        list_analysis(
            app.config["DB_PATH"],
            race_number=race_number,
            category=category,
            has_failures=has_failures,
            search=search,
        )
    )


@app.route("/api/products")
def api_products():
    product_ids = request.args.get("product_ids", "")
    ids = parse_product_ids(product_ids)
    if not ids:
        return jsonify({"error": "product_ids required"}), 400
    catalog_url = app.config.get("CATALOG_URL", DEFAULT_CATALOG)
    try:
        products = fetch_product_raw(ids, catalog_url)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"products": products})


@app.route("/api/detail/<int:analysis_id>")
def api_detail(analysis_id: int):
    row = get_analysis(analysis_id, app.config["DB_PATH"])
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row)


@app.route("/api/notes/<int:analysis_id>", methods=["POST"])
def api_notes(analysis_id: int):
    payload = request.get_json(silent=True) or {}
    notes = str(payload.get("notes", ""))
    ok = update_analysis_notes(analysis_id, notes, app.config["DB_PATH"])
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5057)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--catalog-url", default=DEFAULT_CATALOG)
    args = parser.parse_args()

    init_db(args.db)
    app.config["DB_PATH"] = args.db
    app.config["CATALOG_URL"] = args.catalog_url

    summary = get_summary(args.db)
    url = f"http://{args.host}:{args.port}"
    print(f"Reward analysis DB: {args.db}")
    print(f"  {summary['total_queries']} queries, {summary['queries_with_failures']} with failures")
    print(f"Open {url}")

    if not args.no_browser and args.host in ("127.0.0.1", "localhost"):
        Timer(0.8, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
