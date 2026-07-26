"""Legal Risk Assessment Agent - Analyzes contracts for potential risks and liabilities.

Output is intentionally capped (top 5 risks, short fields, max_tokens limit) to keep
latency low enough for synchronous API Gateway / MCP invocation paths.
"""

import os
import re
import json
from typing import Dict, Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

# Model configuration from environment variable
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Cap generated tokens - this is the primary latency lever for this agent
MAX_TOKENS = int(os.environ.get("RISK_AGENT_MAX_TOKENS", "1500"))

# Maximum number of risks the model should return
MAX_RISKS = int(os.environ.get("RISK_AGENT_MAX_RISKS", "5"))


def extract_json(text: str) -> str:
    """Strip markdown code fences and extract JSON content."""
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


app = BedrockAgentCoreApp()

model = BedrockModel(
    model_id=MODEL_ID,
    max_tokens=MAX_TOKENS,
    temperature=0.3,
)

agent = Agent(
    model=model,
    system_prompt="""You are a Legal Risk Assessment Agent. Identify the most material
legal, financial, and operational risks in a contract and rate their severity
(Critical/High/Medium/Low).

Prioritize: unlimited or uncapped liability, one-sided indemnification, unfavorable
termination terms, excessive penalties, ambiguous language, missing protective clauses,
and regulatory or IP exposure.

Be concise. Return only valid JSON. Never wrap the JSON in markdown code fences.
""",
)


def review_threshold_compliance(risk_data: Dict, threshold: float = 0.7) -> Dict[str, Any]:
    """Review if contract meets risk threshold requirements."""
    try:
        overall_score = float(risk_data.get("overall_risk_score", 0) or 0)
    except (TypeError, ValueError):
        overall_score = 0.0

    if overall_score >= 80:
        risk_level = "Critical"
    elif overall_score >= 60:
        risk_level = "High"
    elif overall_score >= 40:
        risk_level = "Medium"
    else:
        risk_level = "Low"

    exceeds_threshold = (overall_score / 100) > threshold

    return {
        "overall_risk_score": overall_score,
        "risk_level": risk_level,
        "exceeds_threshold": exceeds_threshold,
        "threshold": threshold,
        "recommendation": "Reject" if exceeds_threshold else "Approve for Review",
        "requires_legal_review": exceeds_threshold or overall_score >= 60,
    }


@app.entrypoint
def invoke(payload):
    """AgentCore entrypoint for risk assessment."""
    contract_text = payload.get("contract_text", "")
    extracted_clauses = payload.get("extracted_clauses", None)
    threshold = payload.get("threshold", 0.7)

    if not contract_text:
        return {"error": "No contract text provided"}

    context = f"CONTRACT TEXT:\n{contract_text}"
    if extracted_clauses:
        # Keep prior-agent context compact - full JSON dumps inflate input tokens
        context += f"\n\nPREVIOUSLY EXTRACTED CLAUSES:\n{json.dumps(extracted_clauses)[:2000]}"

    prompt = f"""Assess the legal risks in this contract.

{context}

Return ONLY this JSON structure, with no surrounding text or code fences:

{{
  "overall_risk_score": <integer 0-100, where higher means riskier>,
  "recommendation": "<Approve|Review|Reject>",
  "summary": "<one sentence, max 200 characters>",
  "risks": [
    {{
      "category": "<Financial|Operational|Legal|Compliance|IP>",
      "level": "<Critical|High|Medium|Low>",
      "description": "<max 150 characters>",
      "impact": "<max 100 characters>",
      "mitigation": "<max 120 characters>",
      "clause": "<clause or article reference, or 'N/A'>"
    }}
  ]
}}

Rules:
- Return at most {MAX_RISKS} risks, ordered most severe first.
- Respect the character limits. Do not add fields beyond the schema above.
- Do not repeat the contract text back."""

    result = agent(prompt)

    # Extract text content
    response_text = ""
    if isinstance(result.message, dict) and 'content' in result.message:
        for item in result.message['content']:
            if 'text' in item:
                response_text += item['text']
    else:
        response_text = str(result.message)

    try:
        risk_data = json.loads(extract_json(response_text))

        # Enforce the risk cap even if the model over-produces
        risks = risk_data.get("risks")
        if isinstance(risks, list) and len(risks) > MAX_RISKS:
            risk_data["risks"] = risks[:MAX_RISKS]

        return {
            "status": "success",
            "risk_assessment": risk_data,
            "threshold_analysis": review_threshold_compliance(risk_data, threshold),
            "agent_type": "legal_risk_agent",
        }
    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "Failed to parse risk assessment results",
            "raw_response": response_text[:1000],
            "agent_type": "legal_risk_agent",
        }


if __name__ == "__main__":
    app.run()
