"""Legal Response Agent - Synthesizes analyses and performs direct contract analysis."""

import os
import json
from typing import Dict, Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

# Model configuration from environment variable
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Cap generated tokens - primary latency lever. Sized to allow a full report
# while preventing the model from rambling into a multi-thousand-word document.
MAX_TOKENS = int(os.environ.get("RESPONSE_AGENT_MAX_TOKENS", "2000"))

app = BedrockAgentCoreApp()

model = BedrockModel(
    model_id=MODEL_ID,
    max_tokens=MAX_TOKENS,
    temperature=0.3,
)

agent = Agent(
    model=model,
    system_prompt="""You are a Legal Contract Analysis Agent. Your role is to:
1. Analyze legal contracts comprehensively (clauses, risks, compliance)
2. Synthesize analysis into clear, actionable reports
3. Provide executive summaries suitable for business stakeholders
4. Identify key risks with severity ratings
5. Recommend actions (Approve/Review/Reject)

Your responses should be:
- Clear and concise
- Actionable with specific recommendations
- Include risk levels and priorities
- Structured for easy reading
"""
)


def extract_response_text(result) -> str:
    """Extract text content from agent result."""
    if isinstance(result.message, dict) and 'content' in result.message:
        parts = []
        for item in result.message['content']:
            if 'text' in item:
                parts.append(item['text'])
        return "".join(parts)
    return str(result.message)


def analyze_contract_directly(contract_text: str, user_question: str = None) -> Dict[str, Any]:
    """Perform comprehensive contract analysis directly from raw text."""
    base = f"""Analyze the following legal contract comprehensively.

CONTRACT TEXT:
{contract_text}"""

    if user_question:
        prompt = f"""{base}

USER QUESTION: {user_question}

Provide:
1. Direct answer to the question
2. KEY CLAUSES (type, summary, importance)
3. RISK ASSESSMENT (score 0-100, critical risks, mitigations)
4. COMPLIANCE STATUS (privacy, financial, IP)
5. RECOMMENDATIONS and next steps"""
    else:
        prompt = f"""{base}

Provide a comprehensive analysis:
1. EXECUTIVE SUMMARY (2-3 sentences)
2. KEY CLAUSES (type, summary, importance High/Medium/Low)
3. RISK ASSESSMENT (score 0-100, risks by severity, mitigations)
4. COMPLIANCE STATUS (privacy, financial, employment, IP - score 0-100)
5. RECOMMENDATIONS (immediate actions, clauses to negotiate)
6. DECISION (Approve/Review/Reject with reasoning)"""

    result = agent(prompt)
    response_text = extract_response_text(result)

    return {
        "status": "success",
        "contract_analysis": {
            "analysis": response_text,
            "mode": "direct_analysis",
            "includes_user_question": user_question is not None
        },
        "agent_type": "response_agent"
    }


def generate_summary(clause_analysis: Dict = None, risk_analysis: Dict = None,
                     compliance_analysis: Dict = None, user_question: str = None) -> Dict[str, Any]:
    """Generate summary from pre-analyzed results."""
    # Compact JSON (no indent) keeps input token count down
    context_parts = []
    if clause_analysis:
        context_parts.append(f"CLAUSE ANALYSIS:\n{json.dumps(clause_analysis)}")
    if risk_analysis:
        context_parts.append(f"RISK ANALYSIS:\n{json.dumps(risk_analysis)}")
    if compliance_analysis:
        context_parts.append(f"COMPLIANCE ANALYSIS:\n{json.dumps(compliance_analysis)}")

    context = "\n\n".join(context_parts)

    brevity = (
        "Be concise: use short bullets, no more than 3 bullets per section, "
        "and do not restate the input JSON."
    )

    if user_question:
        prompt = f"""Based on this legal analysis, answer the user's question.

{context}

USER QUESTION: {user_question}

Structure: 1) Direct answer 2) Key findings 3) Risk summary 4) Compliance status 5) Actions 6) Next steps

{brevity}"""
    else:
        prompt = f"""Generate an executive summary from this legal analysis.

{context}

Structure:
1. EXECUTIVE SUMMARY (2-3 sentences)
2. KEY FINDINGS (clauses, risks, compliance issues)
3. RISK ASSESSMENT (level, top risks, mitigations)
4. COMPLIANCE STATUS (score, concerns, actions)
5. RECOMMENDATIONS (immediate + long-term)
6. DECISION (Approve/Review/Reject with reasoning)

{brevity}"""

    result = agent(prompt)
    response_text = extract_response_text(result)

    return {
        "status": "success",
        "summary_response": response_text,
        "agent_type": "response_agent",
        "includes_user_question": user_question is not None
    }


@app.entrypoint
def invoke(payload):
    """AgentCore entrypoint for response generation.

    Handles two modes:
    1. Direct contract analysis: receives contract_text directly
    2. Summary mode: receives pre-analyzed results from other agents
    """
    contract_text = payload.get("contract_text", "")
    clause_analysis = payload.get("clause_analysis", None)
    risk_analysis = payload.get("risk_analysis", None)
    compliance_analysis = payload.get("compliance_analysis", None)
    user_question = payload.get("user_question", None)

    # Direct analysis mode: raw contract text with no pre-analyzed results
    if contract_text and not any([clause_analysis, risk_analysis, compliance_analysis]):
        return analyze_contract_directly(contract_text, user_question)

    # Summary mode: synthesize pre-analyzed results
    return generate_summary(clause_analysis, risk_analysis, compliance_analysis, user_question)


if __name__ == "__main__":
    app.run()
