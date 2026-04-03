import os
import json
from mcp.server.fastmcp import FastMCP
from tracking_plan_parser import parse_tracking_plan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Initialize FastMCP server
mcp = FastMCP("Tracking Plan Server")

@mcp.tool()
def get_tracking_plan(file_path: str = "tracking_plan.xlsx") -> str:
    """
    Reads and parses the Excel tracking plan into a structured JSON schema.
    Returns a JSON string representing the parsed tracking plan events and properties.
    Claude should use this to understand the valid schema.
    """
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
    """
    Reads the SKILL.md file which contains the strict system rules (M0-M7 Mistake codes)
    and the output format contract. Claude MUST use this to know exactly what to audit.
    """
    actual_path = os.path.join(BASE_DIR, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(actual_path):
        return f"Error: Could not find audit rules at {os.path.abspath(actual_path)}"
    
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading audit rules: {str(e)}"

if __name__ == "__main__":
    # Run the server using stdio transport (required for Claude Desktop integration)
    mcp.run()
