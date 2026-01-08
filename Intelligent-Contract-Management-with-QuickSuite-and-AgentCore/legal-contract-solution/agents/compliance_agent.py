from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
import json
from typing import Dict, List, Any

class ComplianceAgent:
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
            system_prompt="""You are a Legal Compliance Agent. Your role is to:
            1. Check contracts against company policies and regulatory requirements
            2. Identify compliance gaps and violations
            3. Ensure adherence to industry standards and regulations
            4. Verify required clauses and terms are present
            5. Flag missing compliance elements
            
            Focus on compliance areas:
            - Data Privacy (GDPR, CCPA, etc.)
            - Financial Regulations (SOX, PCI-DSS, etc.)
            - Employment Law
            - Intellectual Property Protection
            - Industry-specific regulations
            - Corporate governance requirements
            
            Return analysis in JSON format with compliance status and recommendations.
            """
        )
        
    @property
    def entrypoint(self):
        return self.app.entrypoint
    
    def check_compliance(self, contract_text: str, company_policies: List[str], extracted_clauses: Dict = None) -> Dict[str, Any]:
        """Check contract compliance against company policies"""
        
        policies_text = "\n".join([f"- {policy}" for policy in company_policies])
        
        context = f"CONTRACT TEXT:\n{contract_text}"
        if extracted_clauses:
            context += f"\n\nPREVIOUSLY EXTRACTED CLAUSES:\n{json.dumps(extracted_clauses, indent=2)}"
        
        prompt = f"""
        Review the following legal contract for compliance with company policies and regulatory requirements:
        
        {context}
        
        COMPANY POLICIES TO CHECK AGAINST:
        {policies_text}
        
        Please assess compliance in the following areas:
        
        1. DATA PRIVACY COMPLIANCE:
           - GDPR compliance for EU data subjects
           - CCPA compliance for California residents
           - Data processing and storage requirements
           - Data breach notification procedures
           - Right to deletion and portability
        
        2. FINANCIAL REGULATIONS:
           - SOX compliance for financial reporting
           - Anti-money laundering (AML) requirements
           - Payment card industry (PCI-DSS) standards
           - Financial disclosure requirements
        
        3. EMPLOYMENT LAW:
           - Equal opportunity provisions
           - Workplace safety requirements
           - Non-discrimination clauses
           - Wage and hour compliance
        
        4. INTELLECTUAL PROPERTY:
           - IP ownership and licensing terms
           - Trade secret protection
           - Patent and trademark considerations
           - Copyright compliance
        
        5. INDUSTRY-SPECIFIC REGULATIONS:
           - Healthcare (HIPAA) if applicable
           - Financial services regulations
           - Technology and software compliance
           - International trade regulations
        
        For each area, provide:
        - Compliance status (Compliant/Non-Compliant/Partial/Not Applicable)
        - Specific issues identified
        - Missing required clauses
        - Recommended actions
        - Severity level (Critical/High/Medium/Low)
        
        Also provide an overall compliance score (0-100) and summary.
        
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
            compliance_data = json.loads(response_text)
            return {
                "status": "success",
                "compliance_assessment": compliance_data,
                "agent_type": "compliance_agent"
            }
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": "Failed to parse compliance assessment results",
                "raw_response": response_text,
                "agent_type": "compliance_agent"
            }

def create_compliance_agent_app():
    """Create the AgentCore app for the Compliance Agent"""
    compliance_agent = ComplianceAgent()
    
    @compliance_agent.app.entrypoint
    def invoke(payload):
        """AgentCore entrypoint for compliance checking"""
        contract_text = payload.get("contract_text", "")
        company_policies = payload.get("company_policies", [
            "Data Privacy Requirements",
            "Financial Regulations",
            "Employment Law Compliance",
            "Intellectual Property Protection"
        ])
        extracted_clauses = payload.get("extracted_clauses", None)
        
        if not contract_text:
            return {"error": "No contract text provided"}
        
        result = compliance_agent.check_compliance(contract_text, company_policies, extracted_clauses)
        return result
    
    return compliance_agent.app

if __name__ == "__main__":
    app = create_compliance_agent_app()
    app.run()