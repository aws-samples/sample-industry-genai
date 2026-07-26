"""Legal Compliance Agent - Checks regulatory and policy compliance.

Output is intentionally capped (fixed area list, short fields, max_tokens limit) to keep
latency low enough for synchronous API Gateway / MCP invocation paths.
"""

import os
import re
import json
from typing import List

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

# Model configuration from environment variable
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Cap generated tokens - this is the primary latency lever for this agent
MAX_TOKENS = int(os.environ.get("COMPLIANCE_AGENT_MAX_TOKENS", "1500"))

DEFAULT_POLICIES = [
    "Data Privacy Requirements",
    "Financial Regulations",
    "Employment Law Compliance",
    "Intellectual Property Protection",
]


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
    system_prompt="""You are a Legal Compliance Agent. Check contracts against regulatory
and policy requirements, identify gaps, and flag missing required clauses.

Coverage areas: data privacy (GDPR, CCPA), financial regulations (SOX, AML, PCI-DSS),
employment law, intellectual property, and industry-specific rules (HIPAA where relevant).

Be concise. Return only valid JSON. Never wrap the JSON in markdown code fences.
""",
)


@app.entrypoint
def invoke(payload):
    """AgentCore entrypoint for compliance checking."""
    contract_text = payload.get("contract_text", "")
    company_policies: List[str] = payload.get("company_policies", DEFAULT_POLICIES)
    extracted_clauses = payload.get("extracted_clauses", None)

    if not contract_text:
        return {"error": "No contract text provided"}

    policies_text = ", ".join(company_policies)
    context = f"CONTRACT TEXT:\n{contract_text}"
    if extracted_clauses:
        # Keep prior-agent context compact - full JSON dumps inflate input tokens
        context += f"\n\nPREVIOUSLY EXTRACTED CLAUSES:\n{json.dumps(extracted_clauses)[:2000]}"

    prompt = f"""Review this contract for regulatory and policy compliance.

{context}

POLICIES TO CHECK: {policies_text}

Return ONLY this JSON structure, with no surrounding text or code fences:

{{
  "overall_compliance_score": <integer 0-100, where higher means more compliant>,
  "overall_status": "<Compliant|Partial|Non-Compliant>",
  "summary": "<one sentence, max 200 characters>",
  "areas": [
    {{
      "area": "<Data Privacy|Financial Regulations|Employment Law|Intellectual Property|Industry-Specific>",
      "status": "<Compliant|Partial|Non-Compliant|Not Applicable>",
      "severity": "<Critical|High|Medium|Low|None>",
      "issues": "<max 150 characters>",
      "missing_clauses": "<max 120 characters, or 'None'>",
      "action": "<max 120 characters, or 'None'>"
    }}
  ]
}}

Rules:
- Cover exactly the five areas listed above, once each, in that order.
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
        compliance_data = json.loads(extract_json(response_text))
        return {
            "status": "success",
            "compliance_assessment": compliance_data,
            "agent_type": "compliance_agent",
        }
    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "Failed to parse compliance assessment results",
            "raw_response": response_text[:1000],
            "agent_type": "compliance_agent",
        }


if __name__ == "__main__":
    app.run()
