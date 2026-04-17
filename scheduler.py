# scheduler.py
"""
Kaliper Headless Scheduler - Final Revision

Runs a complete audit cycle with state-aware metrics and performance heartbeats.
"""

import os
import json
import time
from datetime import datetime, timedelta
import schedule
from dotenv import load_dotenv

import mcp_tools
import audit_engine
import alert_engine
from gemini_agent import run_gemini_audit_agent

load_dotenv()

CONFIG = {
    "project_name":    os.getenv("PROJECT_NAME", "Kaliper Fashion"),
    "mode":            os.getenv("MODE", "live").lower(),
    "tracking_plan":   os.getenv("TRACKING_PLAN_PATH", "tracking_plan.xlsx"),
    "google_key":      os.getenv("GOOGLE_API_KEY"),
    "slack_webhook":   os.getenv("SLACK_WEBHOOK_URL"),
    "audit_interval":  int(os.getenv("AUDIT_INTERVAL_MINUTES", "1440")),
}

def run_scheduler():
    start_t = time.time()
    print(f"\n--- {CONFIG['project_name']} Audit Start ({CONFIG['mode'].upper()}) ---")

    amp_api_key, amp_secret, amp_pid = os.getenv("AMPLITUDE_API_KEY"), os.getenv("AMPLITUDE_SECRET_KEY"), os.getenv("AMPLITUDE_PROJECT_ID")
    mcp_tools.save_audit_metadata(amp_pid, last_attempt=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if CONFIG["mode"] == "live" and not (amp_api_key and amp_secret and amp_pid):
        raise ValueError("Missing Amplitude credentials for LIVE mode.")

    if CONFIG["mode"] == "demo":
        try:
            with open("simulated_events.json", "r") as f: events = json.load(f)
        except: raise FileNotFoundError("Run simulate.py first.")
    else:
        metadata = mcp_tools.load_audit_metadata()
        last_dt = metadata.get(amp_pid, {}).get("last_audit_date")
        s_dt = datetime.strptime(last_dt, "%Y-%m-%d") if last_dt else (datetime.now() - timedelta(days=3))
        e_dt = datetime.now()
        fetch_res = mcp_tools.execute_get_amplitude_events({"start":s_dt.strftime("%Y%m%dT%H"), "end":e_dt.strftime("%Y%m%dT%H")}, {"api_key":amp_api_key, "secret_key":amp_secret})
        if "error" in fetch_res: return
        events = fetch_res.get("events", [])

    if not events: return {"status": "no_events"}

    engine = audit_engine.AuditEngine(CONFIG["tracking_plan"], events, project_id=amp_pid)
    summary, issues = engine.run_all_checks()

    mcp_tools.save_audit_metadata(amp_pid, last_date=datetime.now())
    mcp_tools.append_audit_history(summary)
    
    triggered = alert_engine.evaluate_alerts(summary, {}, amp_pid)
    has_crit = any(r["severity"] in ["P0", "P1"] for r in triggered)
    
    ai_diag = None
    if has_crit and CONFIG["google_key"]:
        try:
            gen = run_gemini_audit_agent(CONFIG["google_key"], "Analyze quality gaps.", CONFIG["tracking_plan"], issues, events, {"api_key":amp_api_key, "secret_key":amp_secret, "project_id":amp_pid})
            for step in gen:
                if step["type"] == "report": ai_diag = step["report"]
        except: pass

    alert_engine.dispatch_alerts(triggered, summary, {"slack_webhook": CONFIG["slack_webhook"], "project_name": CONFIG["project_name"], "ai_diagnosis": ai_diag})

    duration = round(time.time() - start_t, 2)
    mcp_tools.save_audit_metadata(amp_pid, last_success_duration=f"{duration}s")
    print(f"[scheduler] Cycle Complete. Health: {summary['health_score']}% | Time: {duration}s")
    return {"status": "success"}

def start_loop():
    schedule.every(CONFIG["audit_interval"]).minutes.do(run_scheduler)
    run_scheduler()
    while True:
        try:
            schedule.run_pending()
            time.sleep(10)
        except: time.sleep(60)

if __name__ == "__main__":
    start_loop()
