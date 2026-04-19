# scheduler_v2.py
"""
Kaliper V2 Scheduler - Pipeline Orchestrator

The single point of execution for the deterministic governance pipeline.
Assembles the final 'audit_output.json' contract from modular components.

Flow: fetcher_v2 -> audit_engine_v2 -> state_engine_v2 -> Assembler -> output
"""

import json
import os
import time
from datetime import datetime
from fetcher_v2 import FetcherV2
from tracking_plan_parser_v2 import TrackingPlanParserV2
from audit_engine_v2 import AuditEngineV2
from state_engine_v2 import StateEngineV2

# Constants
TRACKING_PLAN = "tracking_plan.xlsx"
OUTPUT_FILE   = "audit_output.json"

def run_pipeline(mode="simulation", days_back=1):
    start_time = time.time()
    print(f"\n[scheduler_v2] Starting pipeline in {mode} mode...")

    # 1. Fetch
    events = FetcherV2.fetch(mode=mode, days_back=days_back)
    print(f"[scheduler_v2] Ingested {len(events)} events.")

    # 2. Parse Rules
    parser = TrackingPlanParserV2()
    schema = parser.parse(TRACKING_PLAN)

    # 3. Audit (Stateless)
    engine = AuditEngineV2(schema)
    audit_res = engine.run(events)
    print(f"[scheduler_v2] Audit complete. Score: {audit_res['summary']['health_score']}")

    # 4. State (History/Lifecycle)
    state_engine = StateEngineV2()
    stateful_clusters = state_engine.apply_lifecycle(audit_res["issue_clusters"])
    trend = state_engine.get_health_trend()

    # 5. Assemble Final Contract (audit_output.json)
    # Sort top issues by weighted penalty
    stateful_clusters.sort(key=lambda x: x["weighted_penalty"], reverse=True)
    
    top_driver = stateful_clusters[0] if stateful_clusters else None
    top_20 = stateful_clusters[:20]

    end_time = time.time()
    
    final_output = {
        "summary": {
            "health_score":         audit_res["summary"]["health_score"],
            "total_events":         audit_res["summary"]["total_events"],
            "weighted_penalty":    audit_res["summary"]["weighted_penalty"],
            "unknown_platform_pct": audit_res["summary"]["unknown_platform_pct"],
            "total_issues":        len(stateful_clusters)
        },
        "issues": stateful_clusters,
        "top_issues": top_20,
        "top_driver": top_driver,
        "trend": trend,
        "metadata": {
            "timestamp":      datetime.now().isoformat(),
            "window_days":    days_back,
            "mode":           mode,
            "success_duration": round(end_time - start_time, 2),
            "source_file":    TRACKING_PLAN
        }
    }

    # 6. Atomic Write of Contract
    temp_path = OUTPUT_FILE + ".tmp"
    with open(temp_path, "w") as f:
        json.dump(final_output, f, indent=2)
    os.replace(temp_path, OUTPUT_FILE)
    
    # 7. Update History Registry
    state_engine.update_history(audit_res["summary"], stateful_clusters)

    print(f"[scheduler_v2] Pipeline success. Output written to {OUTPUT_FILE}\n")
    return final_output

if __name__ == "__main__":
    # Test run
    import os
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    run_pipeline(mode="simulation")
