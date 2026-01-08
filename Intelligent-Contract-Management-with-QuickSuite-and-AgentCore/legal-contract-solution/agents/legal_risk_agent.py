from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
import json
from typing import Dict, List, Any

class LegalRiskAgent:
    def __init__(self, model_id: str = None):
        # Use fallback model detection if no model specified
        if model_id is None:
            from config import BEDROCK_MODEL_ID, get_available_model
            try:
                model_id = get_available_model()
            except:
                model_id = BEDROCK_MODEL_ID
        
        self.app = BedrockAgentCoreApp()
        self.agent = Agent(
            model=model_id,
            system_prompt="""You are a Legal Risk Assessment Agent. Your role is to:
            1. Analyze legal contracts for potential risks and liabilities
            2. Identify unfavorable terms and conditions
            3. Flag clauses that could lead to legal disputes
            4. Assess financial and operational risks
            5. Provide risk severity ratings (Critical/High/Medium/Low)
            
            Focus on identifying:
            - Unlimited liability clauses
            - Unfavorable termination terms
            - Excessive penalties or damages
            - Unclear or ambiguous language
            - Missing protective clauses
            - Regulatory compliance risks
            - Intellectual property risks
            
            Return analysis in JSON format with risk level, description, and recommendations.
            """
        )
        
    @property
    def entrypoint(self):
        return self.app.entrypoint
    
    def assess_risks(self, contract_text: str, extracted_clauses: Dict = None) -> Dict[str, Any]:
        """Assess legal risks in the contract"""
        
        context = f"CONTRACT TEXT:\n{contract_text}"
        if extracted_clauses:
            context += f"\n\nPREVIOUSLY EXTRACTED CLAUSES:\n{json.dumps(extracted_clauses, indent=2)}"
        
        prompt = f"""
        Analyze the following legal contract for potential risks and liabilities:
        
        {context}
        
        Please assess and identify:
        1. Critical risks that could result in significant financial loss
        2. High risks that could impact business operations
        3. Medium risks that require attention but are manageable
        4. Low risks that are standard or acceptable
        
        For each risk identified, provide:
        - Risk category (Financial, Operational, Legal, Compliance, etc.)
        - Risk level (Critical/High/Medium/Low)
        - Description of the risk
        - Potential impact
        - Recommended mitigation actions
        - Specific clause reference if applicable
        
        Also provide an overall risk score (0-100) and recommendation (Approve/Review/Reject).
        
        Format your response as a JSON object.
        """
        
        result = self.agent(prompt)
        
        # Extract text content from the result
        response_text = ""
        if isinstance(result.message, dict) and 'content' in result.message:
            for content_item in result.message['content']:
                if 'text' in content_item:
                    response_text += content_item['text']
        else:
            response_text = str(result.message)
        
        try:
            risk_data = json.loads(response_text)
            return {
                "status": "success",
                "risk_assessment": risk_data,
                "agent_type": "legal_risk_agent"
            }
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": "Failed to parse risk assessment results",
                "raw_response": response_text,
                "agent_type": "legal_risk_agent"
            }
    
    def review_threshold_compliance(self, risk_assessment: Dict, threshold: float = 0.7) -> Dict[str, Any]:
        """Review if contract meets risk threshold requirements"""
        
        overall_score = risk_assessment.get("risk_assessment", {}).get("overall_risk_score", 0)
        risk_level = "Low"
        
        if overall_score >= 80:
            risk_level = "Critical"
        elif overall_score >= 60:
            risk_level = "High"
        elif overall_score >= 40:
            risk_level = "Medium"
        
        exceeds_threshold = (overall_score / 100) > threshold
        
        return {
            "overall_risk_score": overall_score,
            "risk_level": risk_level,
            "exceeds_threshold": exceeds_threshold,
            "threshold": threshold,
            "recommendation": "Reject" if exceeds_threshold else "Approve for Review",
            "requires_legal_review": exceeds_threshold or overall_score >= 60
        }

def create_legal_risk_agent_app():
    """Create the AgentCore app for the Legal Risk Agent"""
    risk_agent = LegalRiskAgent()
    
    @risk_agent.app.entrypoint
    def invoke(payload):
        """AgentCore entrypoint for risk assessment"""
        contract_text = payload.get("contract_text", "")
        extracted_clauses = payload.get("extracted_clauses", None)
        threshold = payload.get("threshold", 0.7)
        
        if not contract_text:
            return {"error": "No contract text provided"}
        
        # Assess risks
        risk_result = risk_agent.assess_risks(contract_text, extracted_clauses)
        
        if risk_result["status"] == "success":
            # Check threshold compliance
            threshold_result = risk_agent.review_threshold_compliance(
                risk_result, threshold
            )
            risk_result["threshold_analysis"] = threshold_result
        
        return risk_result
    
    return risk_agent.app

if __name__ == "__main__":
    app = create_legal_risk_agent_app()
    app.run()