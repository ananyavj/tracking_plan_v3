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
    # Support both list format and dict/lookup format
    events_source = tp.get("events", [])
    if not events_source and "event_lookup" in tp:
        # Convert lookup dict to list for the summarizer
        events_source = list(tp["event_lookup"].values())

    for ev in events_source:
        ename = ev.get("event_name", "Unknown")
        props_list = []
        for p in ev.get("properties", []):
            pname = p.get("name", "unknown")
            ptype = p.get("type", "any")
            preq  = "req" if p.get("required") else "opt"
            hint = ""
            desc_lower = p.get("description", "").lower()
            for key, val in HINT_MAP.items():
                if key in pname.lower() or key in desc_lower:
                    hint = f" ({val})"
                    break
            props_list.append(f"{pname}[{ptype},{preq}]{hint}")
        
        summary.append(f"- {ename}: {', '.join(props_list)}")
    
    return "\n".join(summary) if summary else "No events found in tracking plan."

def calculate_complexity(findings, tracking_plan):
    summary = findings.get("audit_summary", {})
    issues  = findings.get("clustered_findings", [])
    if len(issues) > 12: return True
    critical_codes = {"M3", "M4", "M6"}
    found_codes = {i.get("code") for i in issues}
    if found_codes.intersection(critical_codes): return True
    if len(tracking_plan.get("events", [])) > 25: return True
    return False

# Severity badge colours keyed by mistake code
_SEVERITY_PALETTE = {
    "M0": ("#ff4444", "UNKNOWN EVENT"),
    "M1": ("#ff8c00", "TYPE MISMATCH"),
    "M2": ("#ff4444", "MISSING PROP"),
    "M3": ("#ff8c00", "FUNNEL BREAK"),
    "M4": ("#ff4444", "JOURNEY BREAK"),
    "M5": ("#ffcc00", "CALC ERROR"),
    "M6": ("#ffcc00", "IDENTITY"),
    "M7": ("#aaaaaa", "DUPLICATE"),
    "M8": ("#ff8c00", "ENUM VIOLATION"),
}

def _build_html_report(data: dict) -> str:
    recs         = data.get("recommendations", [])
    gaps         = data.get("tracking_plan_gaps", [])
    meta         = data.get("audit_meta", {})
    summary_text = data.get("summary", data.get("executive_summary", ""))

    # ── Severity config ──────────────────────────────────────────────────────
    _VERDICT_CONFIG = {
        "typo":          {"label": "TYPO",         "bg": "#2a1a00", "color": "#ffaa33", "icon": "⚠"},
        "new_feature":   {"label": "NEW FEATURE",  "bg": "#001a2e", "color": "#4facfe", "icon": "✦"},
        "test_artifact": {"label": "TEST ARTIFACT","bg": "#1a1a1a", "color": "#888899", "icon": "○"},
        "missing":       {"label": "MISSING",      "bg": "#2a0a0a", "color": "#ff5555", "icon": "✕"},
        "inconsistent":  {"label": "INCONSISTENT", "bg": "#1e1400", "color": "#ffcc00", "icon": "≠"},
    }
    _CODE_CONFIG = {
        "M0": {"label": "UNKNOWN EVENT",  "bg": "#2a0a0a", "color": "#ff5555"},
        "M1": {"label": "TYPE MISMATCH",  "bg": "#2a1500", "color": "#ff8c00"},
        "M2": {"label": "MISSING PROP",   "bg": "#2a0a0a", "color": "#ff5555"},
        "M3": {"label": "FUNNEL BREAK",   "bg": "#2a1500", "color": "#ff8c00"},
        "M4": {"label": "JOURNEY BREAK",  "bg": "#2a0a0a", "color": "#ff5555"},
        "M5": {"label": "CALC ERROR",     "bg": "#1e1400", "color": "#ffcc00"},
        "M6": {"label": "IDENTITY",       "bg": "#1e1400", "color": "#ffcc00"},
        "M7": {"label": "DUPLICATE",      "bg": "#1a1a1a", "color": "#888899"},
        "M8": {"label": "ENUM VIOLATION", "bg": "#2a1500", "color": "#ff8c00"},
    }

    def _severity_chip(label, bg, color):
        return f"""<span style='display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;background:{bg};color:{color};font-size:11px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;font-family:monospace;border:1px solid {color}44'>{label}</span>"""

    # ── Recommendation cards ─────────────────────────────────────────────────
    def _rec_card(idx, r):
        if isinstance(r, str):
            title, detail, code_fix = r, "", ""
        else:
            title    = r.get("title") or r.get("recommendation") or "Recommendation"
            detail   = r.get("detail") or r.get("description") or ""
            code_fix = r.get("code_fix") or ""

        if detail.lower().strip() == title.lower().strip() or detail.lower() in title.lower():
            detail = ""

        code_key = next((c for c in _CODE_CONFIG if c in title.upper()), None)
        chip_html = ""
        if code_key:
            cfg = _CODE_CONFIG[code_key]
            chip_html = _severity_chip(cfg["label"], cfg["bg"], cfg["color"])

        code_block = ""
        if code_fix:
            escaped = code_fix.replace("<", "&lt;").replace(">", "&gt;")
            code_block = f"""<div style='margin-top:14px;background:#080a10;border:1px solid #1e3020;border-radius:8px;overflow-x:auto'><pre style='padding:14px 16px;margin:0;font-size:12.5px;line-height:1.65;color:#7ec8a0;font-family:"Fira Code","Consolas",monospace'>{escaped}</pre></div>"""

        return f"""
        <div class='rec-card' style='background:#0f1219;border:1px solid #1c2030;border-radius:14px;padding:20px 22px;margin-bottom:12px;transition:border-color 0.2s ease' onmouseover="this.style.borderColor='#2a3d5e'" onmouseout="this.style.borderColor='#1c2030'">
          <div style='display:flex;align-items:flex-start;gap:14px'>
            <div style='flex-shrink:0;width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,#1a4a7a,#0d2d4d);border:1px solid #2a5a9a;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:800;color:#6ab4f5;margin-top:2px'>{idx}</div>
            <div style='flex:1;min-width:0'>
              <div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px'>
                <span style='font-weight:700;color:#dde4f5;font-size:15px;line-height:1.4'>{title}</span>
                {chip_html}
              </div>
              {f'<div style="color:#8896b8;font-size:13.5px;line-height:1.75;margin-top:4px">{detail}</div>' if detail else ''}
              {code_block}
            </div>
          </div>
        </div>"""

    # ── Gap rows ─────────────────────────────────────────────────────────────
    def _gap_row(g):
        name    = g.get("event_name", "Unknown")
        verdict = g.get("verdict", "unknown")
        reason  = g.get("reason", "")
        cfg = _VERDICT_CONFIG.get(verdict.lower(), {"label": verdict.upper(), "bg": "#1a1a2a", "color": "#e94560", "icon": "?"})
        chip = _severity_chip(f"{cfg['icon']} {cfg['label']}", cfg['bg'], cfg['color'])
        return f"""
        <div style='border:1px solid #1c2030;border-radius:12px;padding:16px 20px;margin-bottom:10px;background:#0c0f17'>
          <div style='display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:8px'>
            <code style='color:#88c8f5;font-size:13px;font-family:"Fira Code",monospace;font-weight:600'>{name}</code>
            {chip}
          </div>
          <div style='color:#7a88aa;font-size:13.5px;line-height:1.7'>{reason}</div>
        </div>"""

    recs_html = "\n".join(_rec_card(i + 1, r) for i, r in enumerate(recs))
    gaps_html = "\n".join(_gap_row(g) for g in gaps)

    model_name  = meta.get('model', 'Groq Llama 3.3')
    iterations  = meta.get('iterations', '—')
    tool_calls  = meta.get('tool_calls', '0')

    gaps_section = f"""
    <section style='margin-bottom:36px'>
      <div style='display:flex;align-items:center;gap:10px;margin-bottom:20px'>
        <span style='font-size:18px'>&#x1F4CB;</span>
        <h2 style='margin:0;font-size:17px;font-weight:700;color:#c8d4f0;letter-spacing:0.2px'>Predicted Tracking Plan Gaps</h2>
        <span style='font-size:11px;font-weight:700;letter-spacing:0.8px;color:#e94560;background:#2a0a14;padding:3px 10px;border-radius:20px;border:1px solid #e9456044;text-transform:uppercase'>{len(gaps)} found</span>
      </div>
      <div>{gaps_html}</div>
    </section>""" if gaps else ""

    summary_html = f"""
    <section style='margin-bottom:36px'>
      <div style='border-left:3px solid #2a5a9a;border-radius:0 10px 10px 0;background:#080e1a;padding:18px 22px'>
        <div style='font-size:10px;font-weight:800;letter-spacing:2px;color:#4a7ab5;text-transform:uppercase;margin-bottom:10px'>Executive Overview</div>
        <div style='color:#a8b8d4;font-size:14.5px;line-height:1.85'>{summary_text}</div>
      </div>
    </section>""" if summary_text else ""

    return f"""
<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Fira+Code:wght@400;500&display=swap' rel='stylesheet'>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    font-family: 'Inter', -apple-system, sans-serif;
    background: #080c14;
    color: #c0cce0;
    padding: 28px 32px 48px;
    line-height: 1.6;
    font-size: 14px;
    min-height: 100vh;
  }}
  .report-header {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
    background: #0c1220;
    border: 1px solid #1c2a40;
    border-radius: 16px;
    padding: 24px 28px;
    margin-bottom: 32px;
    position: relative;
    overflow: hidden;
  }}
  .report-header::before {{
    content: '';
    position: absolute;
    top: -40px; right: -40px;
    width: 200px; height: 200px;
    background: radial-gradient(circle, #1a3a6a22 0%, transparent 70%);
    pointer-events: none;
  }}
  .report-title {{
    font-size: 22px;
    font-weight: 700;
    color: #e0ecff;
    letter-spacing: -0.3px;
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
  }}
  .report-subtitle {{ font-size: 12px; color: #4a6080; letter-spacing: 0.5px; }}
  .meta-row {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
  .meta-tag {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: #0e1826;
    border: 1px solid #1a2d45;
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    color: #6a8aaa;
    font-weight: 500;
  }}
  .meta-tag .dot {{ color: #3a7abd; font-size: 10px; }}
  .section-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 18px;
    padding-bottom: 12px;
    border-bottom: 1px solid #141c28;
  }}
  .section-label {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: #3a6090;
  }}
  .section-title {{
    font-size: 16px;
    font-weight: 600;
    color: #c0d0e8;
    margin: 0;
  }}
  .divider {{ height: 1px; background: #10182a; margin: 28px 0; }}
</style>
</head>
<body>

<header class='report-header'>
  <div>
    <div class='report-title'>
      <span style='width:32px;height:32px;border-radius:8px;background:#0e1e36;border:1px solid #1a3050;display:inline-flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0'>&#x1F916;</span>
      Kaliper AI Diagnostic Report
    </div>
    <div class='report-subtitle'>Autonomous analytics governance · Kaliper v3</div>
  </div>
  <div class='meta-row'>
    <div class='meta-tag'><span class='dot'>●</span> {model_name}</div>
    <div class='meta-tag'>Iterations: {iterations}</div>
    <div class='meta-tag'>Tool calls: {tool_calls}</div>
  </div>
</header>

{summary_html}

<section style='margin-bottom:36px'>
  <div class='section-header'>
    <span class='section-label'>Remediation</span>
    <h2 class='section-title'>Recommendations</h2>
  </div>
  {recs_html}
</section>

<div class='divider'></div>

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

    # Aggressive truncation for Free Tier (6000 TPM limit)
    if len(compact_plan) > 1000:
        compact_plan = compact_plan[:1000] + "\n...(truncated for space)"

    sys_prompt_trimmed = (system_prompt + DIAGNOSTICIAN_ADDENDUM)
    if len(sys_prompt_trimmed) > 1500:
        sys_prompt_trimmed = sys_prompt_trimmed[:1500] + "\n..."

    messages = [
        {"role": "system", "content": sys_prompt_trimmed},
        {"role": "user", "content": f"SCHEMA:\n{compact_plan}\n\nTOP ISSUES:\n{json.dumps(top_findings, separators=(',', ':'))[:1000]}\n\nDiagnose now."}
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
                        "content": json.dumps(result)[:1000], # Truncate large tool outputs
                    })
                
                # After processing all tool results, inject a forcing message
                # so the model knows to stop calling tools and write the report
                messages.append({
                    "role": "user",
                    "content": (
                        "You have gathered enough evidence from the tools. "
                        "Now return ONLY valid JSON (no markdown fences) with these exact keys: "
                        "summary (string), recommendations (list, each with title+detail+code_fix), "
                        "tracking_plan_gaps (list, each with event_name+verdict+reason). "
                        "Include at least 3 concrete recommendations based on the issues found."
                    )
                })
                continue  # Go to next iteration to get the final JSON response


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
