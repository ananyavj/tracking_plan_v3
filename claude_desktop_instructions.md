# Claude Desktop Setup Guide: Amplitude + Tracking Plan Auditor

To execute autonomous tracking plan audits directly in Claude Desktop—without using the Python Streamlit dashboard—you need to configure Claude Desktop to connect to both the official Amplitude MCP server and your local Tracking Plan MCP Server.

## Step 1: Open Claude Desktop Configuration
1. Open Claude Desktop.
2. Go to **Settings** -> **Developer** -> **Edit Config** (or check `Connectors`).
3. If you prefer to manually edit the file, open `%APPDATA%\Claude\claude_desktop_config.json` on Windows.

## Step 2: Configure the JSON File
Replace your configuration file contents with the following JSON block. This points Claude to both the Amplitude NPM package and your new local Python MCP script.

```json
{
  "mcpServers": {
    "amplitude": {
      "command": "npx",
      "args": ["-y", "@amplitude/mcp-server"]
    },
    "tracking_plan": {
      "command": "C:/Users/Ananya/Documents/tracking_plan_v3/venv/Scripts/python.exe",
      "args": ["C:/Users/Ananya/Documents/tracking_plan_v3/tracking_mcp_server.py"]
    }
  }
}
```
*(Make sure Node.js is installed on your system so `npx` works!)*

## Step 3: OAuth Amplitude
1. **Restart Claude Desktop completely** (quit from system tray).
2. When you start your next chat and ask it to use Amplitude, Claude might prompt you to log in to Amplitude via an OAuth popup or Settings panel. Complete this to grant Claude secure access to your Amplitude projects.

## Step 4: Run the Final Audit!
In any Claude Desktop chat, simply copy and paste this exact prompt:

> **"Use your tools to get the tracking plan and my audit rules. Then use your Amplitude connector to pull the latest 300 events for my project, and run a full audit comparing the events to the tracking plan according to the rules."**

Claude will autonomously wake up `tracking_mcp_server.py` to read your Excel file, fetch your live Amplitude data, and report the errors right in your chat!
