from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
import json
from typing import Dict, List, Any

class ResponseAgent:
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
            system_prompt="""You are a Legal Response Agent. Your role is to:
            1. Synthesize analysis from multiple legal agents
            2. Create comprehensive summaries and recommendations
            3. Generate user-friendly reports from technical legal analysis
            4. Provide clear action items and next steps
            5. Format responses for different audiences (legal team, executives, etc.)
            
            Your responses should be:
            - Clear and concise
            - Actionable with specific recommendations
            - Properly formatted for easy reading
            - Include risk levels and priorities
            - Provide both summary and detailed views
            """
        )
        
    @property
    def entrypoint(self):
        return self.app.entrypoint
    
    def generate_summary_response(self, 
                                clause_analysis: Dict = None,
                                risk_analysis: Dict = None, 
                                compliance_analysis: Dict = None,
                                user_question: str = None) -> Dict[str, Any]:
        """Generate a comprehensive summary response from all agent analyses"""
        
        # Prepare context from all analyses
        context_parts = []
        
        if clause_analysis:
            context_parts.append(f"CLAUSE ANALYSIS:\n{json.dumps(clause_analysis, indent=2)}")
        
        if risk_analysis:
            context_parts.append(f"RISK ANALYSIS:\n{json.dumps(risk_analysis, indent=2)}")
        
        if compliance_analysis:
            context_parts.append(f"COMPLIANCE ANALYSIS:\n{json.dumps(compliance_analysis, indent=2)}")
        
        context = "\n\n".join(context_parts)
        
        base_prompt = f"""
        Based on the comprehensive legal analysis provided below, generate a clear and actionable summary report.
        
        {context}
        """
        
        if user_question:
            prompt = f"""
            {base_prompt}
            
            USER QUESTION: {user_question}
            
            Please provide a focused response to the user's specific question while incorporating relevant insights from the legal analysis.
            
            Structure your response as:
            1. Direct answer to the user's question
            2. Key findings from the analysis
            3. Risk assessment summary
            4. Compliance status
            5. Recommended actions
            6. Next steps
            """
        else:
            prompt = f"""
            {base_prompt}
            
            Please provide a comprehensive executive summary of the legal contract analysis.
            
            Structure your response as:
            1. EXECUTIVE SUMMARY (2-3 sentences)
            2. KEY FINDINGS
               - Important clauses identified
               - Critical risks discovered
               - Compliance issues found
            3. RISK ASSESSMENT
               - Overall risk level
               - Top 3 risks to address
               - Risk mitigation recommendations
            4. COMPLIANCE STATUS
               - Overall compliance score
               - Areas of concern
               - Required actions
            5. RECOMMENDATIONS
               - Immediate actions required
               - Long-term considerations
               - Legal review requirements
            6. DECISION RECOMMENDATION
               - Approve/Review/Reject with reasoning
            
            Make the response clear, actionable, and suitable for both legal and business stakeholders.
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
        
        return {
            "status": "success",
            "summary_response": response_text,
            "agent_type": "response_agent",
            "includes_user_question": user_question is not None
        }
    
    def generate_notification(self, analysis_results: Dict, notification_type: str = "review") -> Dict[str, Any]:
        """Generate notifications for legal team or stakeholders"""
        
        prompt = f"""
        Based on the legal contract analysis results, generate a {notification_type} notification.
        
        ANALYSIS RESULTS:
        {json.dumps(analysis_results, indent=2)}
        
        Generate a professional notification that includes:
        1. Subject line
        2. Priority level (High/Medium/Low)
        3. Brief summary of findings
        4. Action required
        5. Timeline/urgency
        6. Key stakeholders to involve
        
        Format as a structured notification suitable for email or internal messaging systems.
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
        
        return {
            "status": "success",
            "notification": response_text,
            "notification_type": notification_type,
            "agent_type": "response_agent"
        }

def create_response_agent_app():
    """Create the AgentCore app for the Response Agent"""
    response_agent = ResponseAgent()
    
    @response_agent.app.entrypoint
    def invoke(payload):
        """AgentCore entrypoint for response generation"""
        clause_analysis = payload.get("clause_analysis", None)
        risk_analysis = payload.get("risk_analysis", None)
        compliance_analysis = payload.get("compliance_analysis", None)
        user_question = payload.get("user_question", None)
        action_type = payload.get("action_type", "summary")  # summary or notification
        
        if action_type == "notification":
            notification_type = payload.get("notification_type", "review")
            analysis_results = {
                "clause_analysis": clause_analysis,
                "risk_analysis": risk_analysis,
                "compliance_analysis": compliance_analysis
            }
            result = response_agent.generate_notification(analysis_results, notification_type)
        else:
            result = response_agent.generate_summary_response(
                clause_analysis, risk_analysis, compliance_analysis, user_question
            )
        
        return result
    
    return response_agent.app

if __name__ == "__main__":
    app = create_response_agent_app()
    app.run()