# Kaliper — State-Aware Analytics Governance System

> **Kaliper** is a production-grade analytics monitoring system that enforces 100% compliance between a company's Tracking Plan (the source of truth) and its live event streams from Amplitude. It combines a deterministic Python audit engine, a stateful alert registry, an autonomous AI diagnostician powered by Groq (Llama 3.3), and two user interfaces — a Streamlit dashboard and a Claude Desktop MCP integration.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Overview](#2-architecture-overview)
3. [The Deterministic Audit Engine](#3-the-deterministic-audit-engine)
4. [The Stateful Alert System](#4-the-stateful-alert-system)
5. [The AI Diagnostician (Groq + Llama 3.3)](#5-the-ai-diagnostician-groq--llama-33)
6. [Interface Layer 1 — Live Debugger (app.py / V1)](#interface-layer-1--live-debugger-apppy--v1)
7. [Interface Layer 2 — Governance Dashboard (app_v2.py / V2)](#interface-layer-2--governance-dashboard-app_v2py--v2)
8. [Interface Layer 3 — Claude Desktop MCP Integration](#interface-layer-3--claude-desktop-mcp-integration)
9. [The V2 Deterministic Pipeline](#9-the-v2-deterministic-pipeline)
10. [Mathematical Foundations](#10-mathematical-foundations)
11. [Project File Reference](#11-project-file-reference)
12. [Setup & Running](#12-setup--running)

---

## 1. Problem Statement

Most analytics monitoring tools only catch **volume anomalies** — "we got 30% fewer events today." Kaliper goes deeper. It catches **silent data quality regressions** that volume monitors completely miss:

- A mobile SDK update starts sending `price` as a string `"799"` instead of a float `799.0`. Revenue charts still populate. Amplitude never throws an error. But every revenue aggregation is now wrong.
- An iOS checkout flow gets a session management bug. Orders complete without a `Checkout Started` event in the same session. Funnel analysis breaks entirely.
- A backend deploy silently drops `session_id` from all events on one platform. Attribution models collapse.

**Kaliper catches all of this** — automatically, at scale, against a formal schema, with AI-powered root cause diagnosis.

---

## 2. Architecture Overview

Kaliper operates on a **"Governed Hybrid"** architecture that strictly separates two concerns:

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA SOURCES                             │
│   Amplitude Export API  ·  Local JSON Upload                │
└───────────────────┬─────────────────────────────────────────┘
                    │ up to 40,000+ events
                    ▼
┌─────────────────────────────────────────────────────────────┐
│              DETERMINISTIC ENGINE (audit_engine.py)         │
│  100% scan · 0% AI · mathematically reproducible scores     │
│  Checks: M0 M1 M2 M3 M4 M5 M6 M7 M8                        │
└───────────────────┬─────────────────────────────────────────┘
                    │ structured findings JSON
          ┌─────────┴──────────┐
          ▼                    ▼
┌─────────────────┐  ┌──────────────────────────────────────┐
│  ALERT ENGINE   │  │       AI DIAGNOSTICIAN               │
│  alert_engine.py│  │  groq_agent.py (Llama 3.3-70B)       │
│  Lifecycle      │  │  Tool-calling · Root cause analysis  │
│  State tracking │  │  Tracking plan gap detection         │
│  Slack dispatch │  └──────────────────────────────────────┘
└────────┬────────┘
         ▼
┌─────────────────────────────────────────────────────────────┐
│                   INTERFACE LAYERS                          │
│  app.py (V1 Microscope)  ·  app_v2.py (V2 Satellite)        │
│  tracking_mcp_server.py (Claude Desktop MCP)               │
└─────────────────────────────────────────────────────────────┘
```

The fundamental principle: **the Python engine is always the source of truth. The AI is a forensic analyst, never the auditor.**

---

## 3. The Deterministic Audit Engine

**File:** `audit_engine.py`  
**Input:** A list of Amplitude events + a parsed tracking plan  
**Output:** A summary dict + a flat list of every issue found

### 3.1 Tracking Plan Parsing

The tracking plan lives in `tracking_plan.xlsx`. The parser (`tracking_plan_parser.py`) reads each sheet and builds a normalized lookup:

```python
{
  "event_lookup": {
    "product_added": {          # normalized: lowercase + underscores
      "event_name": "Product Added",
      "properties": [
        { "name": "cart_id",  "required": True,  "type": "string" },
        { "name": "price",    "required": True,  "type": "float"  },
        { "name": "currency", "required": True,  "type": "string",
          "allowed_values": ["INR"] }
      ]
    }
  },
  "global_props": [
    { "name": "session_id", "required": True,  "type": "string" },
    { "name": "platform",   "required": True,  "type": "string",
      "allowed_values": ["web", "ios", "android"] }
  ]
}
```

### 3.2 The 9 Audit Checks (M0–M8)

| Code | Name | Severity | Description |
|------|------|----------|-------------|
| **M0** | Unknown Event | Critical | Event name not in tracking plan after normalization |
| **M1** | Type Mismatch | Critical | Property value is wrong type (e.g. `"799"` sent as string for a `float` field) |
| **M2** | Missing Required Property | Critical | A `required: true` field is entirely absent from the event |
| **M3** | Inconsistent Product ID | Critical | Single-view → single-add session where viewed product ≠ added product |
| **M4** | Funnel Break | Critical | `Order Completed` fired in a session with no preceding `Checkout Started` |
| **M5** | Calculation Error | Warning | `discount_pct` doesn't match `(compare_at_price - price) / compare_at_price * 100` within 1% tolerance |
| **M6** | User State Inconsistency | Warning | `is_first_order=true` on a user who has prior orders in the dataset |
| **M7** | Duplicate Event | Warning | Same `insert_id` appears more than once |
| **M8** | Enum Violation | Warning | Property value not in the `allowed_values` list (e.g. `platform: "desktop"` when only `web/ios/android` allowed) |

### 3.3 Quad-Key Deduplication

Every issue is identified by a **Quad-Key**:

```
(ErrorCode : EventName : PropertyName : Platform)

Example: "M2:Product Added:cart_id:ios"
```

This is critical for signal quality. Without this, a single SDK bug that drops `cart_id` on iOS would show up as thousands of raw issues. With Quad-Key dedup, it collapses to **one cluster** with a count of affected unique events — eliminating alert fatigue.

### 3.4 Blast Radius

Instead of raw error counts, Kaliper tracks **Unique Event Impact**:

```python
blast_radius = len(unique_event_ids_affected) / total_events * 100
```

If one schema bug hits 3 properties on every `Product Added` event, the blast radius is still just the % of `Product Added` events — not tripled.

---

## 4. The Stateful Alert System

**File:** `alert_engine.py`

Every issue cluster is classified against the last 5 audit runs:

| Lifecycle State | Meaning | Signal Weight |
|----------------|---------|--------------|
| 🆕 **New** | First time seen | Medium — investigate |
| 📍 **Persistent** | Present in the previous run | High — team hasn't fixed it |
| 🔄 **Regression** | Was fixed before, now back | Critical — something regressed |

This transforms raw counts into **actionable engineering signals**. A Persistent P0 issue is categorically more urgent than a New one.

### Alert Dispatch

When issues are detected, Kaliper dispatches a **Slack webhook** with a prioritized, ranked issue registry:

```
Kaliper Governance Alert - 2026-04-19 18:30 UTC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Health Score: 97.0% (📉 -1.5%)
Trend History: 99 → 98 → 97 → 97 → 97
Unknown Platform: 12.3% (WARNING)
Audited Events: 36,907

Primary Quality Driver: M2:Product Added

Prioritized Issue Registry:
• 🔄 [P0] M4:Order Completed::ios   (Regression)
• 📍 [P0] M2:Product Added:session_id:unknown   (Persistent)
• 🆕 [P1] M1:Product Viewed:price:android   (New)
```

**Two-stage sort:** Issues are sorted first by `(Severity × Lifecycle weight)`, then by `WeightedPenalty` — so a Persistent P0 always outranks a New P1.

---

## 5. The AI Diagnostician (Groq + Llama 3.3)

**Files:** `groq_agent.py` (full tool-calling agent), `groq_agent_v2.py` (pure reasoning agent)  
**Model:** `llama-3.3-70b-versatile` via Groq API (free tier, ~10x faster than GPT-4)

### Why AI is Phase 2, Not Phase 1

The Python engine produces exact counts. The AI's job is **why** — root cause diagnosis that Python cannot do deterministically:

- Is an M4 funnel break a **real instrumentation gap** or a **cross-device session split** (user started checkout on mobile, completed on web)?
- Is an M0 unknown event a **typo** (`"Prodct Added"`), a **new feature** nobody added to the tracking plan yet, or a **test artifact**?

### 5.1 The Tool-Calling Agent (groq_agent.py)

The full agent implements an **OpenAI-compatible tool-calling loop**:

```
System Prompt (SKILL.md) + Top 20 Issue Clusters
        ↓
    Groq API Call (llama-3.3-70b-versatile)
        ↓
  Does the model want to call a tool?
  ┌────────YES──────────────────────────────────────────────────────┐
  │  inspect_data()          → event type breakdown, property list  │
  │  query_distribution()    → value distribution for a property    │
  │  get_user_history()      → fetch full user history from Amplitude│
  └──────────────────────── loop back to Groq ─────────────────────┘
        ↓
       NO → parse JSON report → validate → yield to app
```

**Token budget management:** The agent automatically truncates findings to the top 20 clusters by frequency, caps the system prompt at 3,000 chars, and falls back to `llama-3.1-8b-instant` if the 70B model hits rate limits.

### 5.2 Output Contract

The agent returns a strictly-typed JSON that the Streamlit app renders directly:

```json
{
  "summary": "Executive summary of root causes...",
  "recommendations": [
    {
      "title": "Fix session_id instrumentation on unknown platform events",
      "detail": "The 180 M2 violations on session_id across all event types suggest the SDK's session middleware is not initializing before the first event fires on cold app starts.",
      "code_fix": "analytics.identify({ session_id: getOrCreateSession() })"
    }
  ],
  "tracking_plan_gaps": [
    { "event_name": "Search Filter Applied", "verdict": "new_feature", "reason": "Consistent naming pattern, no typos, high volume suggests intentional instrumentation" }
  ],
  "html_report": "<full self-contained HTML...>"
}
```

---

## 6. Interface Layer 1 — Streamlit Dashboard (app.py)

**Run with:** `streamlit run app.py`

The primary operator dashboard. It is the only layer that orchestrates all components together.

### Data Modes

| Mode | Description |
|------|-------------|
| **Live Amplitude Fetch** | Pulls directly from Amplitude Export API. Handles ZIP → GZIP → NDJSON decompression. Supports date range filtering. Processes 30,000–50,000 events. |
| **Local JSON Upload** | Upload a pre-exported events JSON for offline/faster auditing |
| **Simulation** | Uses the local `simulated_events.json` (36,907 synthetic events with injected bugs) for demo/testing |

### Key Sidebar Controls

- **Tracking Plan Upload**: Override the default `tracking_plan.xlsx` with any Excel file
- **Groq API Key**: Entered once, used for all AI diagnosis requests
- **Snap to Latest Events**: Auto-discovers the most recent data available in Amplitude
- **High-Precision Mode**: Toggle between `llama-3.3-70b-versatile` (deep reasoning) and `llama-3.1-8b-instant` (faster)
- **Date Filter**: Optionally filter events to a specific window before auditing

### The Audit → Diagnose Flow

```
[Run Full Volume Audit]
      ↓
  AuditEngine.run_all_checks()
  → 36,907 events × 9 checks
  → summary + issues stored in session_state
  → metrics displayed (Health %, Total Issues, Critical, Warnings)
      ↓
[Diagnose with AI]  ← enabled as soon as audit_summary is set
      ↓
  Build top-20 issue clusters from audit_issues
  → run_groq_audit_agent() generator
  → AI calls tools if needed (tool_trace displayed in UI)
  → Final JSON report rendered as HTML in Streamlit components
  → Downloadable as .html report
```

---

## 7. Interface Layer 2 — V2 Governance Dashboard (app_v2.py)

**Run with:** `streamlit run app_v2.py`

A read-only visualization dashboard that consumes the `audit_output.json` contract produced by the V2 deterministic pipeline. Follows the **"Deterministic First"** model — no AI in the rendering layer.

### Features

- Health score trend chart (Plotly)
- Issue lifecycle registry (New / Persistent / Regression) with multi-axis filtering
- Per-issue deep-dive with `GroqAgentV2` localized diagnosis (pure reasoning, no tools)
- One-click pipeline re-run

---

## 8. Interface Layer 3 — Claude Desktop MCP Integration

**File:** `tracking_mcp_server.py`  
**Register in:** `claude_desktop_config.json`

This allows Claude Desktop to act as a fully autonomous analytics auditor. Claude uses the local MCP tools to pull data, run the audit engine, and produce a diagnosis — all from a natural language prompt.

### Exposed MCP Tools

| Tool | Description |
|------|-------------|
| `get_tracking_plan()` | Reads `tracking_plan.xlsx` and returns the full parsed schema as JSON |
| `audit_amplitude_direct(days_back)` | Fetches N days of live Amplitude events and runs the full deterministic audit. Designed for 10k–50k events — bypasses LLM context limits entirely |
| `run_comprehensive_audit(events)` | Audits a provided list of events (for smaller/targeted checks) |
| `query_data_distribution(property_name)` | Returns value distribution for a specific property (e.g. `platform`, `session_id`) |
| `inspect_data()` | Returns high-level dataset metadata — event type counts, user count, all properties seen |

### The Claude Desktop Prompt

```
I need a full analytics governance report for our fashion marketplace tracking plan.

Please do the following autonomously:

1. Call get_tracking_plan to load our Excel schema.
2. Call audit_amplitude_direct with days_back=7 to fetch and audit the last 7 days of production events.
3. For any high-count issue categories, call query_data_distribution to understand which platforms are most affected.
4. Produce a prescriptive report covering:
   - How many events were audited and what % are non-compliant
   - A breakdown of every violation type with counts and examples
   - Root cause diagnosis for each issue category
   - Platform-level breakdown (web vs iOS vs Android)
   - Prioritised fix list with concrete code-level guidance
   - Prevention recommendations (CI checks, SDK guardrails, monitoring alerts)
```

---

## 9. The V2 Deterministic Pipeline

The V2 pipeline is a fully modular, headless governance system with strict separation of concerns. Each component is a pure function with no side effects.

```
scheduler_v2.py
     ↓
fetcher_v2.py          → pulls raw events from Amplitude Export API
     ↓
tracking_plan_parser_v2.py  → parses Excel schema into a platform-aware rule set
     ↓
audit_engine_v2.py     → stateless validator, returns clustered issue JSON
     ↓
state_engine_v2.py     → compares against history, assigns New/Persistent/Regression
     ↓
audit_output.json      → the single source of truth (consumed by app_v2.py)
```

The V2 engine supports **platform-specific rule overrides** — a property can be `required` on iOS but `optional` on web, and the engine handles this without blending violations.

---

## 10. Mathematical Foundations

### Health Score Formula

To avoid arbitrary thresholds, the health score is entirely self-deriving:

```python
MAX_PENALTY_PER_EVENT = max(PENALTY_WEIGHTS.values())  # = 10 (M0, M2, M4)

weighted_penalty = sum(
    issue_cluster_count * PENALTY_WEIGHTS[code]
    for code in found_issue_codes
)

max_possible_penalty = total_events * MAX_PENALTY_PER_EVENT

health_score = 100 * max(0, 1 - weighted_penalty / (max_possible_penalty + 1e-9))
```

The `+ 1e-9` prevents division by zero on empty datasets. The health score is always in `[0, 100]` and is directly comparable across datasets of different sizes.

### Severity Weights

| Code | Weight | Rationale |
|------|--------|-----------|
| M0, M2, M4 | 10 | Schema unknown / missing required / funnel break — highest business impact |
| M1, M3 | 5 | Type mismatch / product inconsistency — impacts analytics accuracy |
| M5, M6, M7 | 2 | Calculation errors, state issues, duplicates — important but lower blast radius |
| M8 | 1 | Enum violation — data hygiene, rarely breaks dashboards |

---

## 11. Project File Reference

```
tracking_plan_v3/
├── app.py                      # Streamlit V1 dashboard (primary UI)
├── app_v2.py                   # Streamlit V2 governance dashboard
├── audit_engine.py             # Core deterministic audit engine (M0–M8)
├── audit_engine_v2.py          # V2 stateless engine (platform-aware rules)
├── alert_engine.py             # Lifecycle classification + Slack dispatch
├── groq_agent.py               # Full tool-calling AI diagnostician (Groq)
├── groq_agent_v2.py            # Pure reasoning agent for V2 drill-down
├── mcp_tools.py                # MCP bridge functions (audit, fetch, inspect)
├── tracking_mcp_server.py      # FastMCP server for Claude Desktop
├── tracking_mcp_server_v2.py   # V2 MCP server (reads audit_output.json)
├── tracking_plan_parser.py     # Excel → JSON schema parser (V1)
├── tracking_plan_parser_v2.py  # Excel → JSON schema parser (V2, platform-aware)
├── fetcher_v2.py               # Amplitude Export API fetcher (V2 pipeline)
├── scheduler.py                # V1 headless scheduler (scan→score→alert loop)
├── scheduler_v2.py             # V2 headless scheduler (full pipeline runner)
├── state_engine_v2.py          # Issue lifecycle classification (V2)
├── utils.py                    # Time parsing, platform extraction helpers
├── simulate.py                 # Synthetic event generator (36k events, injected bugs)
├── check_groq_models.py        # Utility: lists Groq models available to your key
├── SKILL.md                    # AI agent system prompt (860 lines, full audit protocol)
├── Audit_config.json           # Audit configuration (checks, date range, filters)
├── tracking_plan.xlsx          # The tracking plan (source of truth)
└── requirements.txt            # Python dependencies
```

---

## 12. Setup & Running

### Prerequisites

```bash
pip install -r requirements.txt
```

**requirements.txt:**
```
streamlit
pandas
openpyxl
plotly
python-dotenv
requests
groq
mcp
```

### Environment Variables (`.env`)

```bash
GROQ_API_KEY=gsk_your_key_from_console.groq.com
GROQ_MODEL=llama-3.3-70b-versatile

AMPLITUDE_API_KEY=your_amplitude_api_key
AMPLITUDE_SECRET_KEY=your_amplitude_secret_key
AMPLITUDE_PROJECT_ID=your_project_id

SLACK_WEBHOOK_URL=https://hooks.slack.com/...  # optional
```

Get a free Groq API key at [console.groq.com](https://console.groq.com).

### Run the Streamlit App

```bash
streamlit run app.py       # V1 full-feature dashboard
streamlit run app_v2.py    # V2 read-only governance dashboard
```

### Run the V2 Pipeline Headlessly

```bash
python scheduler_v2.py
```

### Register with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kaliper": {
      "command": "python",
      "args": ["C:/path/to/tracking_plan_v3/tracking_mcp_server.py"]
    }
  }
}
```

---

> Built for analytics engineering teams who treat their data pipeline with the same rigor as their source code.

---

## 13. Pipeline Comparison: V1 vs. V2

Kaliper provides two distinct operational modes to handle different stages of the analytics lifecycle.

### Interface Layer 1: The "Microscope" (app.py / V1)
**Best for:** Developers and QA Engineers.
*   **Mode:** Live, Exploratory, Ad-hoc.
*   **Behavior:** Fetches a small-to-medium batch of events directly from Amplitude or local JSON on demand.
*   **State:** Stateless. Every refresh is a clean slate. It does not remember yesterday's bugs.
*   **AI Goal:** "Forensic Analysis"—deep diving into a specific session or event to find out exactly what went wrong.

### Interface Layer 2: The "Satellite" (app_v2.py / V2)
**Best for:** Analytics Managers and Executives.
*   **Mode:** Background, Stateful, Governance.
*   **Behavior:** Runs headlessly via `scheduler_v2.py`. It processes tens of thousands of events and persists the results to `audit_output.json`.
*   **State:** **State-Aware.** It compares today's run against `audit_history.json` to categorize issues:
    *   🔴 **New**: A bug that just appeared for the first time.
    *   🟡 **Persistent**: A bug we already know about that hasn't been fixed yet.
    *   🔵 **Regression**: A bug we once fixed that has mysteriously returned.
*   **AI Goal:** "Strategic Remediation"—high-level summaries of health trends and systemic risks across the entire organization.

---

## 14. Troubleshooting & Maintenance

### Common Issues
*   **Unknown Platform 100%**: This occurs if the `fetcher_v2.py` fails to extract the platform dimension. Ensure that your events contain a `platform` key either at the top level or nested inside `event_properties`. (Note: A fix was applied on April 20th to handle nested Amplitude properties automatically).
*   **Zero Event Audits**: Usually caused by a pathing error in the MCP server. Always use absolute paths for the `TRACKING_PLAN_PATH` in your `.env` file.

---
