# Tracking Plan Auditor: Project Summary & Architecture 

> [!NOTE] 
> This document serves as the absolute source of truth for the Tracking Plan Auditor project. It outlines the objective, methodology, and the two distinct architectural paths developed to achieve it.

## 1. Project Goal
**Goal:** Build a production-grade, autonomous analytics validation engine. Organizations often maintain an external "Tracking Plan" (typically a spreadsheet dictating event schemas) and have actual production data streaming into an analytics provider like Amplitude. Our engine validates how closely the actual implementation aligns with the defined tracking plan, programmatically catching instrumentation bugs.

**Methodology:** We use an advanced Large Language Model (LLM) equipped with explicit, strict domain knowledge (`SKILL.md`). The LLM acts autonomously to cross-reference event data against the tracking plan definitions and flag discrepancies based on predefined mistake typologies (M1 - M6).

## 2. Dual-Path Execution Architecture
To guarantee reliability, bypass rate limits, and provide deployment flexibility, the project achieves this goal through two parallel architectural workflows that share the same validation logic.

### Path A: Claude Desktop native approach (FastMCP)
This is the **Primary Method**, designed for local, direct-agent interaction without requiring a separate web application.
*   **The Engine:** The native Anthropic Claude Desktop application.
*   **The Bridge Server (`tracking_mcp_server.py`):** We deploy a FastMCP (Model Context Protocol) local server. This server exposes internal resources — the `tracking_plan.xlsx` rules and `SKILL.md` instruction set — as named tools that Claude Desktop can call autonomously.
*   **The Amplitude Enterprise MCP Connector:** In addition to the local tracking plan server, Claude Desktop is also connected to the **official `@amplitude/mcp-server`** npm package. This is a production-grade MCP server published by Amplitude itself. It is registered in Claude Desktop's `claude_desktop_config.json` under the `"amplitude"` key and runs via `npx`. When Claude initiates an audit, it authenticates with your Amplitude account via a one-time OAuth popup, which grants it a secure, scoped token. After authentication, Claude can directly call Amplitude's API through this connector — querying events, user properties, and funnel data — without any custom Python HTTP code needed on our side. The two MCP servers (local tracking plan + Amplitude Enterprise) run in parallel, so Claude can fetch both your rules and your live event data in a single autonomous workflow.
*   **Workflow:** The user queries the Claude Desktop client. The client auto-negotiates with `tracking_mcp_server.py` for rules and schemas, and calls `@amplitude/mcp-server` for live event data. Claude then runs the full M0–M6 audit in-context and outputs structured findings directly in the chat window in real-time.

### Path B: Streamlit Web Dashboard (Gemini Fallback)
This is the **Fallback Method**, providing an alternative UI gateway running on Google's Gemini model. It is perfect for cases when Claude hits rate-limits.
*   **The Engine:** Google Gemini connected via `gemini_agent.py`.
*   **The Application UI (`app.py`):** A Streamlit application rendering an interactive dashboard.
*   **The Mock MCP (`mcp_tools.py`):** Since Gemini isn't natively connected to an MCP server, we abstract the identical logic into Python functions that the Gemini LLM can call directly via automatic function calling.
*   **Workflow:** The user uploads the `tracking_plan.xlsx` and their JSON event data to the Streamlit UI. Under the hood, Gemini processes the inputs using `mcp_tools.py` against the `SKILL.md` domain instructions and renders an aesthetic HTML report back to the Streamlit view.

---

## 3. Core Universal Truths (Shared By Both Paths)

No matter which path executes the audit, the underlying truth parameters never change:

1.  **The Tracking Plan (`tracking_plan.xlsx`):** The Excel sheet that defines the event schemas (which properties are required vs. optional, correct types like "string" vs "float", etc.). Parsed universally by `tracking_plan_parser.py`.
2.  **The Audit Rules (`SKILL.md`):** The rigid instruction set preventing the LLM from hallucinating. It specifically dictates the M1 through M6 categorization rules:
    *   **M0:** Invalid Event Name
    *   **M1:** Type Mismatch (e.g., passing a string `"799"` for a float `price`)
    *   **M2:** Missing Required Property
    *   **M3:** Inconsistent Product Data
    *   **M4:** Funnel Break
    *   **M5:** Incorrect Calculation 
    *   **M6:** User State Inconsistency
3.  **The Data Generator Engine (`simulate.py`):** Used primarily to test our LLMs. It generates thousands of realistic simulated events, intentionally injecting specific M-series and logging them exactly in `mistake_log.json`. Our autonomous systems validate their efficacy by attempting to autonomously recreate the same mistake log.

### Appendix: The Local Mock Payload (`claude_ai_sample_events.json`)
The `claude_ai_sample_events.json` file is a lightweight, offline testing payload (approx. 52 KB) containing a subset of the simulated events generated by `simulate.py`. Because the full simulation log can be upwards of 13 MB, this smaller chunk exists specifically to comfortably fit inside LLM context limits during rapid, localized testing. 

*   **In Streamlit (`app.py`)**: You can upload this file into the "Fallback Local events JSON" widget to test the Gemini agent logic without needing an active Amplitude API connection.
*   **In Claude Desktop**: If the official `@amplitude/mcp-server` is unavailable or you are offline, you can manually attach this JSON file into the chat window to simulate the audit locally.

---

## 4. Limitations, Quotas, and Quirks

### 4.1 Token Quotas & The Event Sampler
If using the Gemini Free Tier via Streamlit, there is a hard ceiling of 250,000 input tokens per minute.
* **The Issue:** Attempting to feed the full `tracking_plan.xlsx`, the strict `SKILL.md` rules, and a large event payload in a single execution immediately breaches this 250k free-tier token threshold. 
* **The Fix:** The `mcp_tools.py` event processing logic now uses a **session-preserving sampler** (from `tracking_plan_parser.py`) with a cap of **200 events**. Unlike a naive slice, this sampler prioritizes high-value events (`Order Completed`, `Checkout Started`, mistake-flagged events) and guarantees that if one event from a session is included, ALL events from that session are included — preserving the chronological flow required for M3, M4, and M6 funnel checks.

### 4.2 The "Claude Name via Gemini" Hallucination
During initial testing via Streamlit, the resulting HTML dashboard mysteriously stamped itself with the text: `Model: claude-3-5-sonnet-20241022` and `Generated by Claude Audit Agent`, despite explicitly running on `gemini-flash-latest`.
* **Why it happened:** Large Language Models follow specific structural rules incredibly rigidly. Because the system prompt (`SKILL.md`) was originally written exclusively for Anthropic, its output templates explicitly hardcoded those Claude-branded strings as structural boilerplate examples. 
* **The Result:** Gemini obediently respected the constraints of the prompt, copying the exact Anthropic template text verbatim into its final HTML output structure rather than resolving it as a dynamic engine variable!

---

## 5. Exhaustive Repository File Index

The following is a comprehensive ledger of every file in the repository, explaining its purpose and whether it is tracked in Git or intentionally excluded via `.gitignore`.

### Application Logic (Tracked)
*   **`app.py`**: The main Streamlit web application dashboard allowing users to visualize the audit without needing Claude Desktop.
*   **`gemini_agent.py`**: The intelligence agent wrapper that securely passes tools and prompts to Google's Gemini 1.5 Flash framework.
*   **`tracking_mcp_server.py`**: The FastMCP Python server script that exposes the rules and schemas cleanly so the native Claude Desktop app can use them locally.
*   **`mcp_tools.py`**: Python HTTP abstractions that actually handle fetching data from the Amplitude Export/REST endpoints.
*   **`tracking_plan_parser.py`**: Parses the raw Excel `tracking_plan.xlsx` into strict JSON format understandable by LLMs.
*   **`simulate.py`**: The raw data generator engine built using Faker to deploy massive payloads of realistic data (and injected bugs) for testing.

### Rules & Configuration (Tracked)
*   **`SKILL.md`**: The brain of the operation. This is the exhaustive system prompt containing all rigorous instructions and the M0–M7 evaluation rubric.
*   **`Audit_config.json`**: Basic runtime settings defining whether session checks are enabled, truncation limits, etc.
*   **`project_summary.md`**: You are reading it now! The exhaustive source of truth for the project.
*   **`claude_desktop_instructions.md`**: Contains the exact JSON object you need to inject into Claude Desktop's config file to bind our MCP server.

### Template & Sample Data (Tracked)
*   **`tracking_plan.xlsx`**: The primary reference schema in Excel format. *Note: The file currently tracked is a 25KB fashion e-commerce template. Users should overwrite this file directly with their company's tracking plan when adopting this tool.*
*   **`claude_ai_sample_events.json`**: An officially tracked, lightweight (~52KB) standard fallback template for fashion e-commerce events. 
    *   *Why it is tracked (Not gitignored):* We explicitly track this file so developers cloning the repo can instantly run offline tests in the Streamlit app's "Fallback Upload" box without needing their own Amplitude API keys. It serves as a generic template and should be replaced with your own fallback template of choice if necessary.

### Synthetic Data (Gitignored)
*   **`simulated_events.json`**: A gigantic 13.7MB output of the `simulate.py` script containing ~40,000 synthetic events. Excluded via `.gitignore` to prevent GitHub repository bloat.
*   **`mistake_log.json`**: (~140KB) The verification manifest generated dynamically by `simulate.py` during every run. Excluded because it constantly overwrites itself during testing and is meaningless outside of the local environment.

