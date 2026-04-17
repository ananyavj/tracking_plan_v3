# tracking_mcp_server.py
import os
import json
from mcp.server.fastmcp import FastMCP
from tracking_plan_parser import parse_tracking_plan
from mcp_tools import (
    execute_run_comprehensive_audit,
    execute_get_session_count,
    execute_query_data_distribution,
    execute_inspect_simulated_data
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
def get_audit_rules(file_path: str = "SKILL.md") -> str:
    """Reads the SKILL.md file which contains the M1-M7 rules."""
    actual_path = os.path.join(BASE_DIR, file_path) if not os.path.isabs(file_path) else file_path
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading rules: {str(e)}"

@mcp.tool()
def run_comprehensive_audit(events: list = None) -> str:
    """
    REQUIRED: Performs a zero-bias audit of events locally.
    Claude: Fetch events from Amplitude first, then pass them here!
    Returns a full summary of M0-M7 errors found across the dataset.
    """
    result = execute_run_comprehensive_audit({"events": events}, {})
    return json.dumps(result, indent=2)

@mcp.tool()
def get_session_count(events: list = None) -> str:
    """Calculates total unique sessions (session_id) in the provided or default logs."""
    result = execute_get_session_count({"events": events}, {})
    return json.dumps(result, indent=2)

@mcp.tool()
def query_data_distribution(property_name: str, events: list = None) -> str:
    """
    Returns a breakdown of values for a property (e.g. 'category').
    Use this to answer specific questions like 'How many sarees are there?'.
    """
    result = execute_query_data_distribution({"property_name": property_name, "events": events}, {})
    return json.dumps(result, indent=2)

@mcp.tool()
def inspect_data(events: list = None) -> str:
    """
    REQUIRED: Returns metadata about the events (Event types, User count, Materials, Brands).
    Call this to 'make a mental note' of what's in the data before answering deep interrogation questions.
    """
    result = execute_inspect_simulated_data({"events": events}, {})
    return json.dumps(result, indent=2)

if __name__ == "__main__":
    mcp.run()
