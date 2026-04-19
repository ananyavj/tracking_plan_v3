# gemini_agent_v2.py
"""
Kaliper V2 Gemini Agent - Pure Reasoning Layer

Autonomous diagnostic engine that interprets structured audit results.
Follows the "Execution Lock" directive: Pure reasoning function.
No tool-calling, no data fetching.

Input: Issue cluster JSON + Summary context.
Output: Diagnostic report (Root Cause, Impact, suggested Fix).
"""

import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv(override=True)

class GroqAgentV2:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY is missing.")
            
        self.client = Groq(api_key=self.api_key)
        self.model = os.getenv("GROQ_MODEL", "llama3-70b-8192")

    def diagnose(self, issue_json, summary_json):
        """
        Pure reasoning function using Groq Llama 3. No side effects.
        """
        prompt = f"""
        You are the Kaliper Analytics Diagnostic Agent.
        Your task is to provide a forensic root-cause analysis for a specific tracking issue.

        ### CONTEXT DATA
        Overall Project Summary:
        {json.dumps(summary_json, indent=2)}

        Specific Issue Finding:
        {json.dumps(issue_json, indent=2)}

        ### INSTRUCTIONS
        1. Analyze the 'dedup_key' and error message to identify the root cause.
        2. Evaluate the 'blast_radius' and 'weighted_penalty' to define the impact.
        3. Provide a concrete, actionable technical suggestion to fix the instrumentation.
        4. Do NOT speculate on data you haven't seen.
        5. Do NOT suggest using tools; you are in a tool-less diagnostic mode.

        ### REQUIRED OUTPUT FORMAT (JSON)
        {{
          "root_cause": "Detailed explanation of why this error is occurring...",
          "impact": "Description of how this affects business metrics or funnel analysis...",
          "suggested_fix": "Clear technical steps for the engineering team..."
        }}

        Output ONLY the valid JSON block.
        """

        try:
            completion = self.client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=self.model,
                response_format={"type": "json_object"},
            )
            
            text = completion.choices[0].message.content.strip()
            return json.loads(text)
        except Exception as e:
            return {
                "error": f"Diagnostic failed: {str(e)}",
                "raw_text": completion.choices[0].message.content if 'completion' in locals() else None
            }

if __name__ == "__main__":
    # Test reasoning
    agent = GroqAgentV2()
    
    test_issue = {
        "dedup_key": "M4:Order Completed:None:ios",
        "event": "Order Completed",
        "platform": "ios",
        "count": 45,
        "unique_events": 45,
        "blast_radius": 12.5,
        "weighted_penalty": 450,
        "example_issue": "Order without Checkout Start",
        "lifecycle": "Regression"
    }
    
    test_summary = {
        "health_score": 78.5,
        "total_events": 45000,
        "unknown_platform_pct": 2.1
    }
    
    print("\n[agent_v2] Running diagnostic test...")
    result = agent.diagnose(test_issue, test_summary)
    print(json.dumps(result, indent=2))
