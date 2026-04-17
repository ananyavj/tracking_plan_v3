# gemini_agent.py
"""
Phase 3   Gemini Diagnostician Agent

Role change: Gemini is NO LONGER an auditor. The Python engine already
scanned 100% of events deterministically. Gemini's job is now:

  1. Receive pre-computed clustered findings (grouped by code/event/platform)
  2. Diagnose root causes   especially WHY a bug exists, not just THAT it exists
  3. Decide autonomously when to call get_user_history (M4/M6 cross-device check)
  4. Reason about M0 unknown events: typo vs new feature vs test artifact
  5. Generate concrete, code-level fix recommendations

The agent runs a tool-calling loop and returns the SKILL.md output contract.

FIX 2 — Vertex AI key detection:
  If the API key starts with "AQ.", it is a Vertex AI service account token,
  not an AI Studio key. The google-generativeai library cannot use it.
  We detect this early and yield a structured error sentinel instead of
  crashing silently inside genai.configure().

FIX 4 — Honest "Ground-Verified" tag:
  get_user_history fetches data OUTSIDE the audited batch (it hits the
  Amplitude User Activity API live). We now track this separately so
  app.py can display an accurate provenance label on the verification badge.

FIX 5 — Tool response format: google-generativeai >= 0.5 no longer accepts
  genai.types.ContentDict / PartDict / FunctionResponseDict as the tool
  response message. The correct approach is to pass a plain dict matching
  the expected Content structure, or use the response object from the SDK.
  We now build the function_response turn as a plain dict so it works
  across all recent library versions.

FIX 6 — Model availability: gemini-2.0-pro-exp-02-05 is frequently
  unavailable (quota / deprecation). Cascade order is now:
    force_pro=True  -> gemini-1.5-pro   (stable Pro tier)
    auto-complex    -> gemini-1.5-pro
    default         -> gemini-2.0-flash (or GEMINI_MODEL env override)
  with a hard Flash fallback if any Pro init fails.
"""

import google.generativeai as genai
import json
import re
import os
from utils import get_dataset_bounds
from mcp_tools import (
    execute_get_amplitude_events,
    execute_get_user_history,
    execute_inspect_data,
    execute_query_data_distribution
)

# --- CONTROLLED VOCABULARY FOR SEMANTIC HINTS ---
HINT_MAP = {
    "price":    "unit_price_after_discount",
    "revenue":  "total_receipt_value_gross",
    "id":       "unique_identifier",
    "token":    "secure_auth_token",
    "currency": "iso_4217_currency_code",
    "email":    "pii_email_address",
    "quantity": "item_count_per_row"
}

def summarize_tracking_plan(tp):
    """
    Ultra-compresses tracking plan into: event|prop:type:req (semantic_hint).
    Strips examples and descriptions to save tokens.
    """
    summary = []
    for ev in tp.get("events", []):
        ename = ev["event_name"]
        for p in ev.get("properties", []):
            pname = p["name"]
            ptype = p.get("type", "any")
            preq  = "req" if p.get("required") else "opt"

            hint = ""
            desc_lower = p.get("description", "").lower()
            for key, val in HINT_MAP.items():
                if key in pname.lower() or key in desc_lower:
                    hint = f" ({val})"
                    break

            summary.append(f"{ename}|{pname}:{ptype}:{preq}{hint}")
    return "\n".join(summary)

def calculate_complexity(findings, tracking_plan):
    """
    Heuristic for auto-escalation to Gemini Pro.
    Returns True if Pro is recommended.
    """
    summary = findings.get("audit_summary", {})
    issues  = findings.get("clustered_findings", [])

    if len(issues) > 12: return True

    critical_codes = {"M3", "M4", "M6"}
    found_codes = {i.get("code") for i in issues}
    if found_codes.intersection(critical_codes): return True

    if len(tracking_plan.get("events", [])) > 25: return True

    return False

def _build_html_report(data: dict) -> str:
    """
    Converts the structured JSON report from Gemini into a self-contained HTML
    page that app.py can render via components.html().
    This means html_report is always populated, even when Gemini doesn't include it.
    """
    recs  = data.get("recommendations", [])
    gaps  = data.get("tracking_plan_gaps", [])
    meta  = data.get("audit_meta", {})
    summary_text = data.get("summary", data.get("executive_summary", ""))

    def _rec_html(r):
        if isinstance(r, str):
            return f"<li><p>{r}</p></li>"
        title    = r.get("title") or r.get("recommendation") or "Recommendation"
        detail   = r.get("detail") or r.get("description") or ""
        code_fix = r.get("code_fix") or ""
        code_block = f"<pre><code>{code_fix}</code></pre>" if code_fix else ""
        return f"<li><strong>{title}</strong><p>{detail}</p>{code_block}</li>"

    def _gap_html(g):
        name    = g.get("event_name", "Unknown")
        verdict = g.get("verdict", "unknown")
        reason  = g.get("reason", "")
        colour  = {"typo": "#f0ad4e", "new_feature": "#5bc0de", "test_artifact": "#777"}.get(verdict, "#e94560")
        return (f"<tr><td>{name}</td>"
                f"<td style='color:{colour};font-weight:600'>{verdict}</td>"
                f"<td>{reason}</td></tr>")

    recs_html = "\n".join(_rec_html(r) for r in recs)
    gaps_html = "\n".join(_gap_html(g) for g in gaps)
    gaps_section = (
        f"<h2>📋 Tracking Plan Gaps</h2>"
        f"<table border='1' cellpadding='6' style='border-collapse:collapse;width:100%'>"
        f"<tr><th>Event</th><th>Verdict</th><th>Reason</th></tr>"
        f"{gaps_html}</table>"
    ) if gaps else ""

    return f"""
<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; padding: 2rem; }}
  h1   {{ color: #4facfe; }} h2 {{ color: #00f2fe; border-bottom: 1px solid #333; padding-bottom:.4rem; }}
  pre  {{ background:#1a1a2e; padding:1rem; border-radius:8px; overflow-x:auto; font-size:.85rem; }}
  code {{ color:#a8ff78; }}
  li   {{ margin-bottom:1rem; line-height:1.6; }}
  table{{ color:#e0e0e0; border-color:#444; }}
  th   {{ background:#1a1a2e; }}
</style>
</head>
<body>
<h1>🤖 Kaliper AI Diagnostic Report</h1>
<p><em>Model: {meta.get('model','—')} · Iterations: {meta.get('iterations','—')} · Tool calls: {meta.get('tool_calls','—')}</em></p>
{'<h2>Executive Summary</h2><p>' + summary_text + '</p>' if summary_text else ''}
<h2>🔧 Recommendations</h2>
<ol>{recs_html}</ol>
{gaps_section}
</body></html>
"""


def validate_and_sanitize_report(raw_json):
    """
    Strict schema validation & auto-correction layer.
    Ensures app.py receives dash-ready keys.
    """
    try:
        data = json.loads(raw_json)
    except: return None, "invalid_json"

    gaps = data.get("tracking_plan_gaps", [])
    if not isinstance(gaps, list): gaps = []

    clean_gaps = []
    for g in gaps:
        if not isinstance(g, dict): continue
        ename   = g.get("event_name") or g.get("name") or g.get("event") or "Unknown"
        verdict = g.get("verdict") or "unknown"
        reason  = g.get("reason") or "No reason provided."

        confidence = "high"
        if len(reason) < 15 or "fine" in reason.lower() or "look" in reason.lower():
            confidence = "low"

        clean_gaps.append({
            "event_name": ename,
            "verdict":    verdict,
            "reason":     reason,
            "confidence": confidence
        })

    data["tracking_plan_gaps"] = clean_gaps

    recs = data.get("recommendations", [])
    if not recs or len(str(recs)) < 50:
        return data, "low_confidence"

    # Generate html_report from structured fields so app.py always finds it
    if "html_report" not in data:
        data["html_report"] = _build_html_report(data)

    return data, "success"


# ---------------------------------------------------------------------------
# System prompt addendum injected at runtime (appended to SKILL.md)
# ---------------------------------------------------------------------------

DIAGNOSTICIAN_ADDENDUM = """
---
## PHASE 3 ROLE: YOU ARE THE DIAGNOSTICIAN, NOT THE AUDITOR

The Python engine has already scanned 100% of events. You will receive
CLUSTERED_AUDIT_FINDINGS   a pre-computed, business-weighted list of issue
groups. Do NOT re-audit. Do NOT fetch raw events to recount bugs.

### Your three jobs:

**Job 1   Root Cause Diagnosis**
For each cluster in CLUSTERED_AUDIT_FINDINGS, state the probable engineering
root cause. Be specific. Example: "M1 on Product Added.price across all
platforms with equal distribution suggests the bug is in a shared backend
serialization layer, not platform-specific SDK code."

**Job 2   Agentic Decision: Cross-Device Session Check**
If you see any M4 cluster (funnel break   Order Completed without Checkout
Started), you MUST call get_user_history for one affected user_id from the
samples. Then:
- If their history shows Checkout Started in a DIFFERENT session_id   diagnosis
  is "cross-device session split, not missing instrumentation." Downgrade severity.
- If no Checkout Started anywhere in their history   diagnosis is "genuine
  instrumentation gap." Keep P0.

NOTE: get_user_history fetches data from OUTSIDE the audited batch via the
Amplitude User Activity API. Any conclusions drawn from it are supplemental
evidence, not re-audits. Do NOT use user history data to change issue counts
in CLUSTERED_AUDIT_FINDINGS.audit_summary.

**Job 3   M0 Gap Detection (Tracking Plan Discovery)**
If any M0 clusters exist (unknown event names), classify each as:
  - "typo"   closely matches an existing event name (e.g. "product_add" vs "Product Added")
  - "new_feature"   plausibly a legitimate new event not yet in the tracking plan
  - "test_artifact"   looks like a debug or QA event (e.g. "test_event", "debug_click")

Return your M0 verdict in the `tracking_plan_gaps` section of the output.

### What NOT to do:
- Do NOT call get_amplitude_events to re-fetch and re-count bugs.
- Do NOT report issue counts that differ from CLUSTERED_AUDIT_FINDINGS.audit_summary.
- Do NOT list more than 5 recommendations.
- Do NOT hallucinate properties or events not in the tracking plan.

### Output:
Return the full SKILL.md JSON output contract with these additions:
- `tracking_plan_gaps`: array of M0 verdicts [{event_name, verdict, reason}]
- Each recommendation must include a `code_fix` string with the actual
  implementation change (not generic advice).
---
"""


# ---------------------------------------------------------------------------
# Helper: build a tool-response turn compatible with all recent genai versions
# ---------------------------------------------------------------------------

def _make_tool_response_message(fn_name: str, result: dict) -> dict:
    """
    FIX 5 — Build the tool-response Content dict in the plain-dict form
    accepted by google-generativeai >= 0.5.x.

    The old genai.types.ContentDict / PartDict / FunctionResponseDict API
    was removed in recent library versions and causes:
        AttributeError: module 'google.generativeai.types' has no attribute 'ContentDict'
    Using a plain Python dict that matches the proto structure works across
    all versions that support function calling.
    """
    return {
        "role": "user",
        "parts": [
            {
                "function_response": {
                    "name":     fn_name,
                    "response": result,
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# Agent runner (generator   yields tool_call / report / error steps)
# ---------------------------------------------------------------------------

def run_gemini_audit_agent(
    google_api_key,
    system_prompt,
    tracking_plan,
    clustered_findings,
    events,
    app_config,
    status_callback=None,
    force_pro=False
):
    """
    Phase 7 Self-Healing Agent.
    Orchestrates between Flash/Pro with autonomous escalation.
    """

    # FIX 2 — Vertex AI key guard: detect AQ. prefix before calling genai.configure().
    if google_api_key and google_api_key.startswith("AQ."):
        yield {"type": "error", "error": "VERTEX_KEY_DETECTED"}
        return

    genai.configure(api_key=google_api_key)

    is_complex = calculate_complexity(clustered_findings, tracking_plan)

    # FIX 6 — Updated model cascade:
    # gemini-2.0-pro-exp-02-05 is deprecated/quota-limited. Use stable IDs.
    if force_pro:
        model_id     = "gemini-2.0-flash-thinking-exp"
        display_name = "2.0 Flash Thinking (High-Precision)"
    elif is_complex:
        model_id     = "gemini-2.0-flash"
        display_name = "2.0 Flash (Auto-Escalated)"
    else:
        model_id     = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        display_name = model_id.replace("gemini-", "").replace("-", " ").title()

    if status_callback:
        status_callback(f"Mode: {display_name} | Complexity: {'High' if is_complex else 'Standard'}")

    # FIX 4 — Track whether get_user_history was called during this run.
    external_history_used = False

    def inspect_data() -> dict:
        """
        Exhaustively scans the active dataset to provide ground-truth metadata
        such as event type breakdowns and present properties.
        Use this to verify if a suspect property actually exists in the data.
        Data source: the audited event batch only.
        """
        return execute_inspect_data({"events": events}, app_config)

    def query_distribution(property_name: str) -> dict:
        """
        Returns the distribution of values for a specific property across the dataset.
        Use this to verify percentages of missing properties or valid values.
        Data source: the audited event batch only.
        """
        return execute_query_data_distribution({"property_name": property_name, "events": events}, app_config)

    def get_user_history(user_id: str) -> dict:
        """
        Fetches the complete event history for a specific user via the Amplitude
        User Activity API. This data comes from OUTSIDE the audited batch —
        it is supplemental evidence for cross-device session investigation only.
        Call this when investigating M4 funnel breaks to check cross-device history.
        Do NOT use the returned events to change issue counts.
        """
        return execute_get_user_history({"user_id": user_id}, app_config)

    # Attempt model creation; fall back to Flash if Pro is unavailable.
    def _create_model(mid):
        return genai.GenerativeModel(
            model_name=mid,
            tools=[inspect_data, query_distribution, get_user_history],
            system_instruction=system_prompt + DIAGNOSTICIAN_ADDENDUM,
            generation_config={"temperature": 0.0},
        )

    model = None
    try:
        model = _create_model(model_id)
    except Exception as e:
        if model_id != "gemini-2.0-flash":
            fallback_id = "gemini-2.0-flash"
            display_name = f"2.0 Flash (fallback — {model_id} unavailable)"
            try:
                model = _create_model(fallback_id)
                model_id = fallback_id
            except Exception as e2:
                yield {"type": "error", "error": f"Model initialization failed: {str(e2)}"}
                return
        else:
            yield {"type": "error", "error": f"Model initialization failed: {str(e)}"}
            return

    compact_plan = summarize_tracking_plan(tracking_plan)
    start_iso, end_iso = get_dataset_bounds(events)

    initial_prompt = f"""
RETURN ONLY RAW JSON. NO PREAMBLE. NO MARKDOWN FENCES.

COMPRESSED_SCHEMA:
{compact_plan}

DATASET_WINDOW: {start_iso or 'Unknown'} to {end_iso or 'Unknown'}
TOTAL_EVENTS: {len(events)}

CLUSTERED_FINDINGS:
{json.dumps(clustered_findings.get('clustered_findings', []))}

INSTRUCTIONS:
1. Diagnose Job 1-3.
2. Ground your reasoning in data. You MUST call tools to verify property distributions or funnel breaks.
3. If data is insufficient for a P0 verdict, return the "insufficient_data" flag.
4. Predicted Gaps keys: ["event_name", "verdict", "reason"]
"""

    chat          = model.start_chat()
    current_msg   = initial_prompt
    iteration     = 0
    total_tool_calls = 0
    tool_trace    = []
    MAX_ITERATIONS = 7
    MAX_TOOL_CALLS = 5

    while iteration < MAX_ITERATIONS:
        iteration += 1
        try:
            response  = chat.send_message(current_msg)
            candidate = response.candidates[0]

            # Check for function call in any part (not just parts[0])
            fn_call_part = None
            for part in candidate.content.parts:
                if hasattr(part, 'function_call') and part.function_call and part.function_call.name:
                    fn_call_part = part
                    break

            if fn_call_part is not None:
                if total_tool_calls >= MAX_TOOL_CALLS:
                    current_msg = "TOOL_LIMIT_REACHED. Please finalize your report based on the evidence collected so far."
                    continue

                call = fn_call_part.function_call
                args = {k: v for k, v in call.args.items()}
                total_tool_calls += 1

                yield {"type": "tool_call", "name": call.name, "args": args}

                # EXECUTION MAPPING
                if call.name == "get_user_history":
                    result = execute_get_user_history(args, app_config)
                    trace_obs = f"Found {result.get('events_returned', 0)} events for user."
                    external_history_used = True
                    tool_trace.append({
                        "tool":        call.name,
                        "target":      str(args),
                        "observation": trace_obs,
                        "external":    True,
                    })

                elif call.name == "inspect_data":
                    result = execute_inspect_data({"events": events}, app_config)
                    trace_obs = f"Events: {list(result.get('event_type_breakdown', {}).keys())[:5]}"
                    tool_trace.append({
                        "tool":        call.name,
                        "target":      str(args),
                        "observation": trace_obs,
                        "external":    False,
                    })

                elif call.name == "query_distribution":
                    result = execute_query_data_distribution({**args, "events": events}, app_config)
                    trace_obs = f"Top values: {list(result.get('top_values', {}).keys())[:3]}"
                    tool_trace.append({
                        "tool":        call.name,
                        "target":      str(args),
                        "observation": trace_obs,
                        "external":    False,
                    })

                else:
                    result    = {"error": f"Unknown tool: {call.name}"}
                    trace_obs = "Error: Unknown tool."
                    tool_trace.append({
                        "tool":        call.name,
                        "target":      str(args),
                        "observation": trace_obs,
                        "external":    False,
                    })

                # FIX 5 — Use plain dict for tool response instead of deprecated ContentDict
                current_msg = _make_tool_response_message(call.name, result)

            else:
                # Final response extraction — find the text part
                raw_text = ""
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text:
                        raw_text = part.text
                        break

                json_match = re.search(r'(\{.*\})', raw_text, re.DOTALL)
                if json_match:
                    raw_text = json_match.group(1)

                report, status = validate_and_sanitize_report(raw_text)

                if status == "low_confidence" and iteration < MAX_ITERATIONS - 1:
                    current_msg = "Your previous diagnosis was too vague. Provide specific evidence from the property distributions for Job 1."
                    continue

                if report:
                    report.setdefault("audit_meta", {})
                    report["audit_meta"].update({
                        "model":       display_name,
                        "iterations":  iteration,
                        "tool_calls":  total_tool_calls,
                        "tool_trace":  tool_trace,
                        "termination": "success" if status == "success" else "low_confidence_cap",
                    })

                    batch_tool_calls = [t for t in tool_trace if not t.get("external")]
                    report["audit_meta"]["verified"] = bool(batch_tool_calls)
                    report["audit_meta"]["external_history_used"] = external_history_used

                    yield {"type": "report", "report": report}
                    return
                else:
                    yield {"type": "error", "error": f"Schema validation failed on: {raw_text[:200]}..."}
                    return

        except Exception as e:
            # FIX 2 cont. — Surface Vertex key sentinel with a friendly message.
            err_str = str(e)
            if "VERTEX_KEY" in err_str or "AQ." in err_str:
                yield {"type": "error", "error": "VERTEX_KEY_DETECTED"}
            else:
                yield {"type": "error", "error": f"Agent Crash: {err_str}"}
            return

    yield {"type": "error", "error": "Max iterations reached without stable output."}
