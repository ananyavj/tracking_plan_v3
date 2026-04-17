import os
import sys
import json

# Add root project dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_tools import execute_run_comprehensive_audit

def verify_dynamic_audit():
    # 1. Create a dummy event with a known mistake (M0: Unknown Type)
    test_events = [
        {
            "event_type": "Mystery Button Clicked",
            "user_id": "user_123",
            "time": 1712918400000,
            "event_properties": {
                "session_id": "sess_001",
                "page_path": "/home"
            }
        }
    ]
    
    print("Testing dynamic audit with dummy M0 event...")
    result = execute_run_comprehensive_audit({"events": test_events}, {})
    
    summary = result.get("audit_summary", {})
    m0_count = summary.get("by_check", {}).get("M0", {}).get("count", 0)
    
    if m0_count == 1:
        print("✅ SUCCESS: MCP tool correctly detected M0 in passed-in events.")
    else:
        print(f"❌ FAILURE: M0 count was {m0_count}, expected 1.")
        # print(json.dumps(result, indent=2))

if __name__ == "__main__":
    verify_dynamic_audit()
