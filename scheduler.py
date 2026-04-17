# scheduler.py
"""
Kaliper Headless Scheduler - Phase 3

Runs a complete audit cycle with NO Streamlit dependency.
Designed to be called by GitHub Actions (or any cron runner) daily.

Workflow:
  1. Read last_audit_date from audit_metadata.json (checkpoint)
  2. Fetch events from Amplitude for the gap period
  3. Run AuditEngine on 100% of fetched events
  4. Save new checkpoint
  5. Evaluate alert rules
  6. Dispatch Slack alerts if rules trigger

Usage:
  python scheduler.py                  # uses .env credentials
  AMPLITUDE_PROJECT_ID=xxx python scheduler.py
"""

import os
import json
from datetime import datetime, timedelta
import time
import schedule
from dotenv import load_dotenv

import mcp_tools
import audit_engine
import alert_engine
from gemini_agent import run_gemini_audit_agent

load_dotenv()

# ---------------------------------------------------------------------------
#  CONFIG SETTINGS
# ---------------------------------------------------------------------------
PROJECT_NAME  = os.getenv("PROJECT_NAME", "Kaliper Fashion")
MODE          = os.getenv("MODE", "live").lower() # 'live' or 'demo'
GOOGLE_KEY    = os.getenv("GOOGLE_API_KEY")
TRACKING_PLAN = os.getenv("TRACKING_PLAN_PATH", "tracking_plan.xlsx")

def run_scheduler():
    print(f"\n--- {PROJECT_NAME} Scheduled Audit Start ({MODE.upper()} MODE) ---")

    #    1. Force Determinism: check for required env vars                      
    amp_api_key    = os.getenv("AMPLITUDE_API_KEY")
    amp_secret_key = os.getenv("AMPLITUDE_SECRET_KEY")
    amp_project_id = os.getenv("AMPLITUDE_PROJECT_ID")

    if MODE == "live" and not (amp_api_key and amp_secret_key and amp_project_id):
        raise ValueError("CRITICAL: Missing Amplitude API keys in LIVE mode. Aborting run.")

    #    2. Fetch Events                                                       
    if MODE == "demo":
        print("[scheduler] Running in DEMO mode. Using simulation data...")
        # Optional: could call simulate.main() here if we want fresh data every time
        try:
            with open("simulated_events.json", "r", encoding="utf-8") as f:
                events = json.load(f)
            start_str = "DEMO_START"
            end_str   = "DEMO_END"
        except FileNotFoundError:
            raise FileNotFoundError("DEMO mode failed: simulated_events.json not found. Run simulate.py first.")
    else:
        # LIVE FETCH (Gap-based)
        metadata = mcp_tools.load_audit_metadata()
        last_dt_str = metadata.get(amp_project_id, {}).get("last_audit_date")
        
        if last_dt_str:
            start_dt = datetime.strptime(last_dt_str, "%Y-%m-%d")
        else:
            start_dt = datetime.now() - timedelta(days=3)
        
        end_dt     = datetime.now()
        
        print(f"[scheduler] Fetching from Amplitude: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}...")
        fetch_res = mcp_tools.execute_get_amplitude_events({
            "start": start_dt.strftime("%Y%m%dT%H"),
            "end":   end_dt.strftime("%Y%m%dT%H")
        }, {"api_key": amp_api_key, "secret_key": amp_secret_key})

        if "error" in fetch_res:
            print(f"  FAILED: {fetch_res['error']}")
            return
        
        events = fetch_res.get("events", [])
        print(f"  Got {len(events)} events.")
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str   = end_dt.strftime("%Y-%m-%d")

    if not events:
        print("[scheduler] No events in window - nothing to audit. Exiting cleanly.")
        return {"status": "no_events"}

    #    3. Run audit engine                                                   
    if not os.path.exists(TRACKING_PLAN):
        print(f"[scheduler] ERROR: tracking plan not found at '{TRACKING_PLAN}'")
        return

    print(f"[scheduler] Running AuditEngine on {len(events):,} events...")
    engine  = audit_engine.AuditEngine(TRACKING_PLAN, events, project_id=amp_project_id)
    summary, issues = engine.run_all_checks()

    health  = summary.get("health_score", "?")
    total_i = summary.get("total_issues", 0)
    print(f"[scheduler] Audit complete - issues: {total_i:,} | health: {health}%")

    #    4. Save checkpoint and history                                        
    mcp_tools.save_audit_metadata(amp_project_id, datetime.now())
    mcp_tools.append_audit_history(summary)
    print(f"[scheduler] Checkpoint and history saved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    #    5. Evaluate alert rules                                               
    triggered = alert_engine.evaluate_alerts(summary, {}, amp_project_id)
    print(f"[scheduler] Alerts triggered: {len(triggered)}")
    
    has_critical = False
    for r in triggered:
        msg = f"  {r.get('emoji', '⚠️')} [{r['severity']}] {r['id']}: {r['description']}"
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode('ascii', 'ignore').decode('ascii'))
        if r["severity"] in ["P0", "P1"]:
            has_critical = True

    #    6. AI Diagnostic Enrichment (Isolated Failure)                        
    ai_diagnosis = None
    if has_critical and GOOGLE_KEY:
        print("[scheduler] Critical alerts detected. Initiating autonomous AI diagnosis...")
        try:
            # Load basic system prompt from sibling file if it exists, or use default
            sys_prompt = "You are the Kaliper AI Diagnostician. Analyze the following audit clusters."
            
            # Simple wrapper to collect agent output
            gen = run_gemini_audit_agent(
                google_api_key=GOOGLE_KEY,
                system_prompt=sys_prompt,
                tracking_plan=TRACKING_PLAN,
                clustered_findings=issues,
                events=events,
                app_config={"api_key": amp_api_key, "secret_key": amp_secret_key, "project_id": amp_project_id}
            )
            
            for step in gen:
                if step["type"] == "report":
                    ai_diagnosis = step["report"]
            
            if ai_diagnosis:
                print(f"  🤖 AI Diagnosis Success ({ai_diagnosis['audit_meta'].get('model')})")
                print(f"  Root Cause: {str(ai_diagnosis.get('recommendations', []))[:150]}...")
        except Exception as e:
            print(f"  ⚠️ AI Enrichment Failed (Isolated): {str(e)}")

    #    7. Dispatch alerts                                                    
    dispatch_results = alert_engine.dispatch_alerts(
        triggered,
        summary,
        {
            "slack_webhook": os.getenv("SLACK_WEBHOOK_URL"),
            "project_name":  PROJECT_NAME,
            "ai_diagnosis":  ai_diagnosis
        }
    )
    print(f"[scheduler] Dispatch results: {dispatch_results}")

    result = {
        "status":        "success",
        "events_audited": len(events),
        "total_issues":   total_i,
        "health_score":   health,
        "alerts_fired":   len(triggered),
        "window_start":   start_str,
        "window_end":     end_str,
    }
    print(f"[scheduler] Run complete: {result['status']} | Health: {health}%")
    return result


def start_production_loop():
    """
    Main entry point for long-running monitoring.
    Designed to be run under a process manager like PM2 or Docker.
    """
    interval = int(os.getenv("AUDIT_INTERVAL_MINUTES", "1440"))
    print(f"[scheduler] BOOT: Starting monitoring service (Interval: {interval}m)")
    
    # Schedule the job
    schedule.every(interval).minutes.do(run_scheduler)
    
    # Run once immediately on start
    run_scheduler()
    
    retry_delay = 60
    while True:
        try:
            schedule.run_pending()
            time.sleep(10)
            retry_delay = 60 # Reset on success
        except Exception as e:
            print(f"[scheduler] LOOP ERROR: {e}")
            print(f"[scheduler] Retrying in {retry_delay}s (Exponential Backoff)")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 900)

if __name__ == "__main__":
    start_production_loop()
