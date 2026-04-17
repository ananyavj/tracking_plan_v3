# tracking_mcp_server.py
import os
import json
from mcp.server.fastmcp import FastMCP
from tracking_plan_parser import parse_tracking_plan
from mcp_tools import (
    execute_run_comprehensive_audit,
    execute_query_data_distribution,
    execute_inspect_data,
    execute_audit_amplitude_direct
)
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

# Initialize FastMCP server
mcp = FastMCP("Tracking Plan Server")

@mcp.tool()
def get_tracking_plan(file_path: str = "tracking_plan.xlsx") -> str:
    """Reads the Excel tracking plan into a structured JSON schema."""
    actual_path = os.path.join(BASE_DIR, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(actual_path):
        return f"Error: Could not find tracking plan at {os.path.abspath(actual_path)}"
    try:
        schema = parse_tracking_plan(actual_path)
        return json.dumps(schema, indent=2)
    except Exception as e:
        return f"Error parsing tracking plan: {str(e)}"

@mcp.tool()
def audit_amplitude_direct(days_back: int = 1) -> str:
    """
    REQUIRED: Performs a high-volume, production-grade audit by fetching data 
    DIRECTLY from Amplitude. Bypasses Claude's memory limits to handle 10k-50k events.
    Use this when you need a REAL health check on live production data.
    """
    result = execute_audit_amplitude_direct({"days_back": days_back}, {})
    return json.dumps(result, indent=2)

@mcp.tool()
def run_comprehensive_audit(events: list = None) -> str:
    """
    Performs a deterministic audit of a PROVIDED list of events.
    Use this for small samples or localized debugging.
    """
    result = execute_run_comprehensive_audit({"events": events}, {})
    return json.dumps(result, indent=2)

@mcp.tool()
def query_data_distribution(property_name: str, events: list = None) -> str:
    """
    Returns a breakdown of values for a specific property (e.g. 'platform').
    Use this to audit specific property distributions or 'Unknown' gaps.
    """
    result = execute_query_data_distribution({"property_name": property_name, "events": events}, {})
    return json.dumps(result, indent=2)

@mcp.tool()
def inspect_data(events: list = None) -> str:
    """
    Returns metadata about the events (Event types, User count, Materials, Brands).
    Call this to get a high-level overview of the dataset before deep-diving.
    """
    result = execute_inspect_data({"events": events}, {})
    return json.dumps(result, indent=2)

if __name__ == "__main__":
    mcp.run()
