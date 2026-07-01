# Weekly Work Log Template

> **How to use:** Copy this file each week (or duplicate the sheet tabs below).
> Fill **Section 1** on Monday, log daily in **Section 2**, close the week in **Sections 3–5**.
> Time to maintain: ~2 min/day + 15 min on Friday.

---

## Section 1 — Week setup (Monday, 5 min)

| Field | Value | Notes |
|-------|-------|-------|
| **Person** | | |
| **Role** | | e.g. backend engineer, PM, analyst |
| **Week** | | e.g. 2026-W12 (Mar 16–20) |
| **Contracted hours** | 40 | Mon–Fri default |
| **Days worked** | 5 | Reduce for PTO/holidays |
| **Week type** | Normal | Normal / Release / On-call / Training |

### Effective capacity calculation

| Line | Item | Hours | Formula / note |
|------|------|-------|----------------|
| A | Contracted hours | | e.g. 40 |
| B | × Availability (days worked ÷ 5) | | e.g. 40 × 1.0 = 40 |
| C | − Fixed meetings & ceremonies | | From calendar |
| D | − Admin / email / coordination | | Typical 1–3 h |
| E | **= Available capacity** | | **B − C − D** |
| F | − Expected interruptions | | Support pings, ad-hoc |
| G | − Reactive reserve (on-call, incidents) | | 0 if none |
| H | − Context-switch buffer (5–15% of E) | | Higher if WIP > 3 |
| I | **= Effective delivery capacity** | | **E − F − G − H** |

**My effective delivery capacity this week:** ______ h

**Healthy WIP limit (for my role):** ______ (IC engineer: 2–3)

---

## Section 2 — Daily log (2 min/day)

| Date | Day | Top focus (1 line) | Deep-work blocks | Interruptions (h) | Energy (1–5) | Notes |
|------|-----|--------------------|------------------|-------------------|--------------|-------|
| | Mon | | | | | |
| | Tue | | | | | |
| | Wed | | | | | |
| | Thu | | | | | |
| | Fri | | | | | |

**Energy key:** 1 = exhausted · 3 = normal · 5 = sharp / high output

**End-of-day prompt (pick one per item touched):**
- Moved forward / Draft / Review / Done / Blocked

---

## Section 3 — Work items (main table)

Add one row per task, artifact, or work stream.

| ID | Task / artifact | Type | Category | Priority | Size | Est (h) | Cplx (1–5) | Uncert (1–5) | Status | % done | Weighted load | Worked amount | Quality (0.5–1.2) | Notes |
|----|-----------------|------|----------|----------|------|---------|------------|--------------|--------|--------|---------------|---------------|-------------------|-------|
| T1 | | | | P_ | | | | | | | | | | |
| T2 | | | | | | | | | | | | | | |
| T3 | | | | | | | | | | | | | | |

### Column reference

**Type:** Artifact · Decision · Analysis · Exploration · Review · Execution · Meeting · Reactive

**Category:** Delivery · Collaboration · Meeting · Reactive · Learning

**Priority weight:** P1=1.5 · P2=1.2 · P3=1.0 · P4=0.8

**Size guide (Est h if unknown):**

| Size | Hours |
|------|-------|
| XS | 0.5–1 |
| S | 2–4 |
| M | 8–16 |
| L | 24–40 |
| XL | 40+ |

**Complexity factor:** 1→1.0 · 2→1.1 · 3→1.2 · 4→1.4 · 5→1.5

**Uncertainty factor:** 1→1.0 · 2→1.05 · 3→1.1 · 4→1.2 · 5→1.3

**Status:** Not started · In progress · Draft · In review · Done · Dropped

### Formulas (spreadsheet)

```text
complexity_factor  = CHOOSE(Cplx, 1.0, 1.1, 1.2, 1.4, 1.5)
uncertainty_factor = CHOOSE(Uncert, 1.0, 1.05, 1.1, 1.2, 1.3)
priority_weight    = CHOOSE(Priority, 1.5, 1.2, 1.0, 0.8)   # P1..P4

weighted_load = Est × priority_weight × complexity_factor × uncertainty_factor

worked_amount =
  IF(Status="Done", Est × quality_factor,
  IF(Status="Dropped", 0,
     Est × (%done/100) × quality_factor))

quality_adjusted_worked = worked_amount × quality_factor
```

---

## Section 4 — WIP tracker (optional, daily)

| Date | Open items | New | Closed | WIP | WIP index |
|------|------------|-----|--------|-----|-----------|
| Mon | | | | | WIP ÷ healthy_WIP |
| Tue | | | | | |
| Wed | | | | | |
| Thu | | | | | |
| Fri | | | | | |

**Avg WIP this week:** ______

---

## Section 5 — Weekly summary (Friday, 15 min)

### Totals

| Metric | Formula | This week |
|--------|---------|-----------|
| Planned raw hours (assigned) | SUM(Est) | |
| Total weighted load | SUM(weighted_load) | |
| Active work streams | COUNT distinct categories in progress | |
| Context penalty | 1 + 0.05 × (streams − 1) | |
| **Adjusted workload** | weighted_load × context_penalty | |
| **Effective capacity** | From Section 1 | |
| **Utilization** | adjusted_workload ÷ effective_capacity | |
| Completed worked amount | SUM(worked where Status=Done) | |
| In-progress worked amount | SUM(partial worked) | |
| **Total worked amount** | completed + in-progress | |
| Unplanned / reactive hours | Logged separately | |
| **Delivery ratio** | total_worked ÷ effective_capacity | |

### Workload score

```text
urgency_ratio = SUM(weighted_load where Priority in P1,P2) / SUM(weighted_load)
wip_index     = avg_WIP / healthy_WIP_limit

workload_score = 0.50 × utilization + 0.30 × wip_index + 0.20 × urgency_ratio
```

| Metric | Value | Flag |
|--------|-------|------|
| Utilization | | |
| WIP index | | |
| Urgency ratio | | |
| **Workload score** | | |
| Delivery ratio | | |

### Interpretation

| Workload score | Meaning |
|----------------|---------|
| < 0.85 | Under-utilized |
| 0.85 – 1.10 | Healthy |
| 1.10 – 1.30 | Heavy |
| > 1.30 | Overloaded — rebalance next week |

| Delivery ratio | Meaning |
|----------------|---------|
| < 0.6 | Blocked, under-utilized, or too much WIP |
| 0.6 – 0.9 | Healthy throughput |
| 0.9 – 1.1 | Fully utilized |
| > 1.1 | Likely underestimating work or unsustainable pace |

### Split by category

| Category | Raw h | Weighted load | Worked amount | % of load |
|----------|-------|---------------|---------------|-----------|
| Delivery | | | | |
| Collaboration | | | | |
| Meeting | | | | |
| Reactive | | | | |
| Learning | | | | |
| **Total** | | | | 100% |

---

## Section 6 — Weekly retrospective (5 min)

**Top 3 completed outputs (artifacts / decisions):**
1.
2.
3.

**What blocked progress?**
-

**What should be deferred or dropped next week?**
-

**WIP target for next week (max open items):**
-

**Effective capacity adjustment for next week:**
- [ ] Normal
- [ ] On-call (−20%)
- [ ] Release week (−25%)
- [ ] PTO / short week
- [ ] Other: ___________

---

## Filled example (one week)

### Section 1 snapshot

| Item | Value |
|------|-------|
| Person | Alex |
| Effective delivery capacity | 26 h |

### Section 3 snapshot

| ID | Task | Priority | Est | Cplx | Status | % | Weighted load | Worked |
|----|------|----------|-----|------|--------|---|---------------|--------|
| T1 | Payment bug fix | P1 | 6 | 4 | Done | 100 | 12.6 | 6 |
| T2 | Reports API | P2 | 12 | 3 | In progress | 50 | 17.3 | 6 |
| T3 | 3 PR reviews | P2 | 4 | 2 | Done | 100 | 5.3 | 4 |
| T4 | Meetings | P3 | 5 | 1 | Done | 100 | 5.0 | 5 |
| T7 | On-call incidents | P1 | 3 | 3 | Done | 100 | 5.4 | 3 |

### Section 5 snapshot

| Metric | Value |
|--------|-------|
| Adjusted workload | ~51 h-units |
| Effective capacity | 26 h |
| Utilization | 196% → **OVERLOAD** |
| Total worked amount | 24 h |
| Delivery ratio | 92% |
| Workload score | ~1.7 |

**Action:** Defer T2 finish to next week; cap WIP at 3.

---

## Google Sheets / Excel — tab layout

**Tab 1: `Setup`** — Section 1 fields + named cells (`effective_capacity`, `healthy_wip`)

**Tab 2: `WorkItems`** — Section 3 table with formula columns

**Tab 3: `Daily`** — Section 2

**Tab 4: `Summary`** — Section 5 pulls from WorkItems via SUMIF

**Tab 5: `Archive`** — Paste weekly summary rows for trends

### Archive row (one line per week)

```text
week | person | effective_capacity | adjusted_workload | utilization | worked_amount | delivery_ratio | workload_score | avg_wip | risk_flag | notes
```

---

## Minimal version (if full template is too heavy)

Track only these 8 columns:

```text
task | type | est_h | priority | status | %_done | worked_h | unplanned?
```

Weekly math:

```text
effective_capacity = 40 − meetings − admin − interruption_reserve
total_worked         = SUM(worked_h) + SUM(unplanned)
utilization          = SUM(est_h for open+planned) / effective_capacity
delivery_ratio       = total_worked / effective_capacity
```

Good enough to start; add weights after 2–3 weeks.
