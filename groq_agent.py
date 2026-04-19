# groq_agent.py
"""
Kaliper Groq Diagnostician Agent

Role: Diagnostic reasoning and verification using Groq's high-speed Llama 3 models.
Replaces the previous Gemini implementation.
"""

from groq import Groq
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
    summary = findings.get("audit_summary", {})
    issues  = findings.get("clustered_findings", [])
    if len(issues) > 12: return True
    critical_codes = {"M3", "M4", "M6"}
    found_codes = {i.get("code") for i in issues}
    if found_codes.intersection(critical_codes): return True
    if len(tracking_plan.get("events", [])) > 25: return True
    return False

def _build_html_report(data: dict) -> str:
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
        confidence = "high" if len(reason) > 15 else "low"
        clean_gaps.append({
            "event_name": ename, "verdict": verdict, "reason": reason, "confidence": confidence
        })
    data["tracking_plan_gaps"] = clean_gaps
    if "html_report" not in data:
        data["html_report"] = _build_html_report(data)
    return data, "success"

DIAGNOSTICIAN_ADDENDUM = """
---
## PHASE 3 ROLE: YOU ARE THE DIAGNOSTICIAN, NOT THE AUDITOR
The Python engine has already scanned 100% of events. You will receive CLUSTERED_AUDIT_FINDINGS.
Your jobs:
1. Root Cause Diagnosis
2. Agentic Decision: Cross-Device Session Check (call get_user_history for M4 issues)
3. M0 Gap Detection (Tracking Plan Discovery)

Output ONLY valid JSON matching the SKILL.md contract.
---
"""

# Tool definitions for Groq (OpenAI-compatible)
GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_data",
            "description": "Exhaustively scans the active dataset to provide ground-truth metadata like event type breakdowns and present properties.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_distribution",
            "description": "Returns the distribution of values for a specific property across the dataset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_name": {"type": "string", "description": "The name of the property to analyze."}
                },
                "required": ["property_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_history",
            "description": "Fetches the complete event history for a specific user via the Amplitude User Activity API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "The unique ID of the user."}
                },
                "required": ["user_id"]
            }
        }
    }
]

def run_groq_audit_agent(
    groq_api_key,
    system_prompt,
    tracking_plan,
    clustered_findings,
    events,
    app_config,
    status_callback=None,
    force_pro=False
):
    client = Groq(api_key=groq_api_key)
    
    is_complex = calculate_complexity(clustered_findings, tracking_plan)
    model_id = os.getenv("GROQ_MODEL", "llama3-70b-8192")
    display_name = f"Groq {model_id}"

    if status_callback:
        status_callback(f"Mode: {display_name} | Complexity: {'High' if is_complex else 'Standard'}")

    external_history_used = False
    total_tool_calls = 0
    tool_trace = []
    MAX_ITERATIONS = 7
    MAX_TOOL_CALLS = 5

    compact_plan = summarize_tracking_plan(tracking_plan)
    start_iso, end_iso = get_dataset_bounds(events)

    # Truncate findings to top 20 by impact to stay under token limits
    all_findings = clustered_findings.get('clustered_findings', [])
    top_findings = sorted(all_findings, key=lambda x: x.get('count', 0), reverse=True)[:20]

    # Cap the compact plan to avoid blowing the context window
    if len(compact_plan) > 2000:
        compact_plan = compact_plan[:2000] + "\n...(truncated)"

    # Cap the system prompt at 1500 chars to save space
    sys_prompt_trimmed = (system_prompt + DIAGNOSTICIAN_ADDENDUM)
    if len(sys_prompt_trimmed) > 3000:
        sys_prompt_trimmed = sys_prompt_trimmed[:3000] + "\n..."

    messages = [
        {"role": "system", "content": sys_prompt_trimmed},
        {"role": "user", "content": f"""SCHEMA:\n{compact_plan}\n\nWINDOW: {start_iso or 'Unknown'} to {end_iso or 'Unknown'}\nEVENTS: {len(events)}\n\nTOP ISSUES (by frequency):\n{json.dumps(top_findings, separators=(',', ':'))}\n\nDiagnose root causes and return JSON per the contract."""}
    ]

    def _create_completion(model):
        return client.chat.completions.create(
            model=model,
            messages=messages,
            tools=GROQ_TOOLS,
            tool_choice="auto",
            temperature=0.0
        )

    print(f"[groq_agent] Starting agent with model {model_id}")
    iteration = 0
    while iteration < MAX_ITERATIONS:
        iteration += 1
        print(f"[groq_agent] Iteration {iteration}...")
        try:
            try:
                response = _create_completion(model_id)
            except Exception as rate_err:
                err_str = str(rate_err)
                if "413" in err_str or "rate_limit" in err_str or "too large" in err_str.lower():
                    fallback = "llama-3.1-8b-instant"
                    print(f"[groq_agent] Token limit hit. Retrying with fallback model: {fallback}")
                    display_name = f"Groq {fallback} (fallback)"
                    response = _create_completion(fallback)
                else:
                    raise
            
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls
            print(f"[groq_agent] Received response. Tool calls: {bool(tool_calls)}")

            if tool_calls:
                messages.append(response_message)
                
                for tool_call in tool_calls:
                    if total_tool_calls >= MAX_TOOL_CALLS:
                        break
                    
                    fn_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    total_tool_calls += 1
                    
                    yield {"type": "tool_call", "name": fn_name, "args": args}

                    result = {}
                    trace_obs = ""
                    external = False

                    if fn_name == "get_user_history":
                        result = execute_get_user_history(args, app_config)
                        trace_obs = f"Found {result.get('events_returned', 0)} events for user."
                        external_history_used = True
                        external = True
                    elif fn_name == "inspect_data":
                        result = execute_inspect_data({"events": events}, app_config)
                        trace_obs = f"Events: {list(result.get('event_type_breakdown', {}).keys())[:5]}"
                    elif fn_name == "query_distribution":
                        result = execute_query_data_distribution({**args, "events": events}, app_config)
                        trace_obs = f"Top values: {list(result.get('top_values', {}).keys())[:3]}"
                    
                    tool_trace.append({
                        "tool": fn_name, "target": str(args), "observation": trace_obs, "external": external
                    })

                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": fn_name,
                        "content": json.dumps(result),
                    })
                
                continue # Go to next iteration to get the response after tool calls

            else:
                # No more tool calls, parse final response
                raw_text = response_message.content
                print(f"[groq_agent] Final text received: {len(raw_text) if raw_text else 0} chars")
                json_match = re.search(r'(\{.*\})', raw_text, re.DOTALL)
                if json_match:
                    raw_text = json_match.group(1)

                report, status = validate_and_sanitize_report(raw_text)
                if report:
                    report.setdefault("audit_meta", {})
                    report["audit_meta"].update({
                        "model": display_name,
                        "iterations": iteration,
                        "tool_calls": total_tool_calls,
                        "tool_trace": tool_trace,
                        "termination": "success",
                        "verified": bool([t for t in tool_trace if not t.get("external")]),
                        "external_history_used": external_history_used
                    })
                    yield {"type": "report", "report": report}
                    return
                else:
                    yield {"type": "error", "error": f"Schema validation failed on: {raw_text[:200]}..."}
                    return

        except Exception as e:
            print(f"[groq_agent] CRASH: {str(e)}")
            yield {"type": "error", "error": f"Agent Crash: {str(e)}"}
            return

    yield {"type": "error", "error": "Max iterations reached."}
