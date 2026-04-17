# alert_engine.py
"""
Kaliper Analytics Alert Engine - Final Revision

Evaluates summaries against state-aware rules, tracks issue lifecycles 
(New, Persistent, Regression), and dispatches prioritized dashboards.
"""

import os
import json
import requests
from datetime import datetime
import mcp_tools

def get_issue_lifecycle(key, history):
    """
    Classifies an issue key against history (last 5 runs).
    Hierarchy: Persistent (last run) -> Regression (previous 4) -> New.
    """
    if not history: return "New"
    
    # history[-2] is the actual previous run summary
    prev_keys = history[-2].get("top_dedup_keys", []) if len(history) >= 2 else []
    if key in prev_keys:
        return "Persistent"
        
    for older_run in history[-6:-2]: 
        if key in older_run.get("top_dedup_keys", []):
            return "Regression"
            
    return "New"

def _get_trend_numeric():
    history = mcp_tools.get_audit_history()
    if not history: return ""
    return " → ".join([str(round(h.get("health_score", 100))) for h in history[-5:]])

def should_alert(summary, prev_summary):
    if not prev_summary: return True
    curr_h, prev_h = summary.get("health_score", 100), prev_summary.get("health_score", 100)
    if curr_h < (prev_h - float(os.getenv("HEALTH_DROP_THRESHOLD", "2.0"))): return True
    curr_keys, prev_keys = summary.get("top_dedup_keys", []), prev_summary.get("top_dedup_keys", [])
    for k in curr_keys:
        if k in prev_keys: return True # Alert on persistence
    return summary.get("critical_issues", 0) > prev_summary.get("critical_issues", 0)

def evaluate_alerts(summary, metadata, project_id):
    # Simplified evaluation based on critical counts and health
    triggered = []
    if summary.get("critical_issues", 0) > 0:
        triggered.append({"severity": "P0", "emoji": "🔴", "id": "m2_critical", "description": "Critical schema violations detected."})
    if summary.get("health_score", 100) < 80:
        triggered.append({"severity": "P1", "emoji": "⚠️", "id": "health_warning", "description": "Data health below acceptable threshold."})
    return triggered

def dispatch_alerts(triggered_rules, summary, config):
    history = mcp_tools.get_audit_history()
    prev_summary = history[-2] if len(history) >= 2 else None
    
    if not should_alert(summary, prev_summary) and not config.get("force"):
        return ["Skipped: Signal quality threshold not met."]

    webhook   = config.get("slack_webhook") or os.getenv("SLACK_WEBHOOK_URL")
    proj_name = config.get("project_name", "Kaliper")
    now       = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    
    health = summary.get("health_score", "?")
    total_ev = summary.get("total_events", "?")
    unknown = summary.get("unknown_platform", {"percent": 0, "severity": "OK"})
    
    h_delta = ""
    if prev_summary:
        diff = round(health - prev_summary.get("health_score", 100), 1)
        if diff != 0: h_delta = f" ({'📉' if diff < 0 else '📈'} {diff}%)"

    top_keys, prio_map = summary.get("top_dedup_keys", []), summary.get("issue_prio_map", {})
    issues = []
    for k in top_keys:
        lifecycle = get_issue_lifecycle(k, history)
        severity = "P0" if k.startswith("M0") or k.startswith("M2") or k.startswith("M4") else "P1"
        l_weight = 3 if lifecycle == "Persistent" else 2 if lifecycle == "Regression" else 1
        s_weight = 2 if severity == "P0" else 1
        issues.append({
            "key": k, "lifecycle": lifecycle, "severity": severity, "penalty": prio_map.get(k, 0),
            "l_weight": l_weight, "s_weight": s_weight,
            "display": f"{'🔄' if lifecycle == 'Regression' else '📍' if lifecycle == 'Persistent' else '🆕'} [{severity}] {k}"
        })

    # TWO-STAGE SORT: Primary(Severity+Lifecycle) -> Secondary(Penalty)
    issues.sort(key=lambda x: (x["s_weight"] * 10 + x["l_weight"], x["penalty"]), reverse=True)

    lines = [
        f"*{proj_name} Governance Alert* - {now}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"*Status Overview*",
        f"• *Health Score:* `{health}%`{h_delta}",
        f"• *Trend History:* `{_get_trend_numeric()}`",
        f"• *Unknown Platform:* `{unknown['percent']}%` ({unknown['severity']})",
        f"• *Audited Events:* {total_ev}",
        "",
        f"*Primary Quality Driver:* `{summary.get('top_driver', {}).get('name', 'None')}`",
        "",
        "*Prioritized Issue Registry:*",
    ]
    for iss in issues[:10]: lines.append(f"• {iss['display']}")

    ai_report = config.get("ai_diagnosis")
    if ai_report:
        lines.append("\n*🤖 Autonomous AI Diagnosis:*")
        if "Root Cause" in ai_report and "Impact" in ai_report:
            lines.append(f"{ai_report.strip()}")
        else:
            lines.append(f"*Root Cause:* Critical regression in {summary.get('top_driver',{}).get('name', 'funnel')}")
            lines.append(f"*Impact:* {ai_report[:150].strip()}...")
            lines.append(f"*Suggested Fix:* Validate triggers on {issues[0]['key'].split(':')[-1] if issues else 'affected platforms'}")

    slack_body = {"text": "\n".join(lines)}
    if webhook:
        try:
            resp = requests.post(webhook, json=slack_body, timeout=8)
            return [f"Slack: {resp.status_code}"]
        except: return ["Slack: Error"]
    else:
        print(f"\n[SLACK_PREVIEW]\n{chr(10).join(lines)}\n[/SLACK_PREVIEW]")
        return ["Console: printed"]
