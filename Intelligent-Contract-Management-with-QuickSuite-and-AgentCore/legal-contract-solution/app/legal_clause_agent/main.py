"""Legal Clause Extraction Agent - Extracts and categorizes key contract clauses.

Output is intentionally capped (top N clauses, short summaries instead of verbatim
clause text, max_tokens limit) to keep latency low enough for synchronous
API Gateway / MCP invocation paths.
"""

import os
import re
import json
from typing import Dict, List, Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

# Model configuration from environment variable
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Cap generated tokens - this is the primary latency lever for this agent
MAX_TOKENS = int(os.environ.get("CLAUSE_AGENT_MAX_TOKENS", "1800"))

# Maximum number of clauses the model should return
MAX_CLAUSES = int(os.environ.get("CLAUSE_AGENT_MAX_CLAUSES", "10"))


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
    system_prompt="""You are a Legal Clause Extraction Agent. Identify the key clauses in a
contract, categorize them by type, rate their importance, and capture the material terms
(amounts, dates, notice periods, caps).

Summarize each clause in your own words - never quote the clause verbatim.

Be concise. Return only valid JSON. Never wrap the JSON in markdown code fences.
""",
)


def extract_key_terms(text: str) -> List[str]:
    """Extract key terms (money, dates, durations) from clause text."""
    money = re.findall(r'\$[\d,]+(?:\.\d{2})?', text)
    dates = re.findall(
        r'\b(?:January|February|March|April|May|June|July|August|September|October|'
        r'November|December)\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}',
        text,
    )
    durations = re.findall(r'\b\d+\s+(?:days?|weeks?|months?|years?)\b', text)
    return (money + dates + durations)[:5]


def parse_text_response(response_text: str) -> List[Dict[str, Any]]:
    """Fallback: derive structured clause data when JSON parsing fails."""
    clause_types = [
        "payment", "termination", "liability", "confidentiality",
        "intellectual property", "governing law", "force majeure",
        "amendment", "dispute resolution", "indemnification",
    ]
    clauses = []
    for section in response_text.split('\n\n'):
        section = section.strip()
        if len(section) <= 20:
            continue

        clause_type = "General"
        for ctype in clause_types:
            if ctype in section.lower():
                clause_type = ctype.title()
                break

        importance = "Medium"
        lowered = section.lower()
        if any(w in lowered for w in ["critical", "must", "shall", "liability", "termination"]):
            importance = "High"
        elif any(w in lowered for w in ["may", "optional", "preferred"]):
            importance = "Low"

        clauses.append({
            "clause_type": clause_type,
            "summary": section[:150],
            "importance": importance,
            "parties_involved": ["Party A", "Party B"],
            "key_terms": extract_key_terms(section),
        })
        if len(clauses) >= MAX_CLAUSES:
            break

    return clauses or [{
        "clause_type": "General Analysis",
        "summary": response_text[:150],
        "importance": "Medium",
        "parties_involved": ["Contracting Parties"],
        "key_terms": [],
    }]


@app.entrypoint
def invoke(payload):
    """AgentCore entrypoint for clause extraction."""
    contract_text = payload.get("contract_text", "")
    if not contract_text:
        return {"error": "No contract text provided"}

    prompt = f"""Extract the key clauses from this contract.

CONTRACT TEXT:
{contract_text}

Return ONLY this JSON structure, with no surrounding text or code fences:

{{
  "clauses": [
    {{
      "clause_type": "<e.g. Payment Terms, Termination, Liability, Indemnification, IP, Confidentiality, Governing Law>",
      "summary": "<what the clause does, in your own words, max 150 characters>",
      "importance": "<High|Medium|Low>",
      "parties_involved": ["<party>", "<party>"],
      "key_terms": ["<amount, date, notice period, or cap>"]
    }}
  ]
}}

Rules:
- Return at most {MAX_CLAUSES} clauses, most important first.
- Summarize in your own words. Do NOT quote clause text verbatim.
- Respect the 150-character summary limit. At most 5 key_terms per clause.
- Do not add fields beyond the schema above."""

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
        clauses_data = json.loads(extract_json(response_text))

        # Enforce the clause cap even if the model over-produces
        clauses = clauses_data.get("clauses")
        if isinstance(clauses, list) and len(clauses) > MAX_CLAUSES:
            clauses_data["clauses"] = clauses[:MAX_CLAUSES]

        return {
            "status": "success",
            "clauses": clauses_data,
            "total_clauses": len(clauses_data.get("clauses", [])),
            "agent_type": "legal_clause_agent",
        }
    except json.JSONDecodeError:
        structured = parse_text_response(response_text)
        return {
            "status": "success",
            "clauses": {"clauses": structured},
            "total_clauses": len(structured),
            "agent_type": "legal_clause_agent",
            "parsed_from_text": True,
        }


if __name__ == "__main__":
    app.run()
