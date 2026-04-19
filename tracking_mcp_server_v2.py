# tracking_mcp_server_v2.py
"""
Kaliper V2 MCP Server - Read-Only Query Interface

Provides a natural language interface over the structured audit results.
Follows the "Execution Lock" directive: No computation, no fetching.

Data Source: audit_output.json
"""

import os
import json
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

# Initialize FastMCP server
mcp = FastMCP("Kaliper Governance V2")

OUTPUT_FILE = os.path.join(BASE_DIR, "audit_output.json")

def _load_audit_data():
    if not os.path.exists(OUTPUT_FILE):
        return {"error": "Audit results not found. Please run scheduler_v2.py first."}
    with open(OUTPUT_FILE, "r") as f:
        return json.load(f)

@mcp.tool()
def get_audit_summary() -> str:
    """
    Returns the high-level health score, total events, and data quality overview.
    Use this to get an immediate sense of the tracking health.
    """
    data = _load_audit_data()
    if "error" in data: return data["error"]
    
    summary = data.get("summary", {})
    metadata = data.get("metadata", {})
    
    return json.dumps({
        "health_score":      summary.get("health_score"),
        "total_events":      summary.get("total_events"),
        "weighted_penalty": summary.get("weighted_penalty"),
        "unknown_platform":  f"{summary.get('unknown_platform_pct')}%",
        "total_issue_types": summary.get("total_issues"),
        "last_audit_at":     metadata.get("timestamp"),
        "audit_mode":        metadata.get("mode")
    }, indent=2)

@mcp.tool()
def get_top_issues() -> str:
    """
    Returns the top 20 issues discovered in the latest audit, 
    sorted by weighted impact and blast radius.
    Includes lifecycle state (New, Persistent, Regression).
    """
    data = _load_audit_data()
    if "error" in data: return data["error"]
    
    return json.dumps(data.get("top_issues", []), indent=2)

@mcp.tool()
def get_health_trend() -> str:
    """
    Returns the health score trend from the last 5 audits.
    Use this to identify if tracking quality is improving or declining.
    """
    data = _load_audit_data()
    if "error" in data: return data["error"]
    
    return json.dumps({
        "trend": data.get("trend", []),
        "current_score": data.get("summary", {}).get("health_score")
    }, indent=2)

@mcp.tool()
def get_issue_details(dedup_key: str) -> str:
    """
    Returns deep-dive metadata for a specific issue key.
    Includes example error messages and platform-specific impact.
    """
    data = _load_audit_data()
    if "error" in data: return data["error"]
    
    issues = data.get("issues", [])
    selected = [i for i in issues if i["dedup_key"] == dedup_key]
    
    if not selected:
        return f"Error: Issue key '{dedup_key}' not found in the latest audit."
    
    return json.dumps(selected[0], indent=2)

@mcp.tool()
def get_top_driver() -> str:
    """
    Returns the single most impactful issue affecting the project.
    """
    data = _load_audit_data()
    if "error" in data: return data["error"]
    
    return json.dumps(data.get("top_driver", {}), indent=2)

if __name__ == "__main__":
    mcp.run()
