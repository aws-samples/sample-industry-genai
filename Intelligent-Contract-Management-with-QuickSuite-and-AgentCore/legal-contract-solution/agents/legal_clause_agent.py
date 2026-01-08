from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
import boto3
import json
from typing import Dict, List, Any

class LegalClauseAgent:
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
            system_prompt="""You are a Legal Clause Extraction Agent. Your role is to:
            1. Analyze legal contracts and identify key clauses
            2. Extract important terms, conditions, and obligations
            3. Categorize clauses by type (payment, termination, liability, etc.)
            4. Highlight critical dates, amounts, and parties involved
            
            Return your analysis in structured JSON format with:
            - clause_type: The category of the clause
            - content: The actual clause text
            - importance: High/Medium/Low
            - parties_involved: Who is affected by this clause
            - key_terms: Important terms or values
            """
        )
        
    @property
    def entrypoint(self):
        return self.app.entrypoint
    
    def extract_clauses(self, contract_text: str) -> Dict[str, Any]:
        """Extract key legal clauses from contract text"""
        prompt = f"""
        Analyze the following legal contract and extract all key clauses.
        
        CONTRACT TEXT:
        {contract_text}
        
        Please identify and extract key clauses, then respond with ONLY a valid JSON object in this exact format:
        
        {{
            "clauses": [
                {{
                    "clause_type": "Payment Terms",
                    "content": "The actual clause text here",
                    "importance": "High",
                    "parties_involved": ["Company", "Employee"],
                    "key_terms": ["$100,000", "30 days"]
                }},
                {{
                    "clause_type": "Termination",
                    "content": "The actual clause text here",
                    "importance": "High",
                    "parties_involved": ["Company", "Employee"],
                    "key_terms": ["30 days notice"]
                }}
            ]
        }}
        
        Focus on these clause types:
        - Payment terms and conditions
        - Termination clauses
        - Liability and indemnification
        - Intellectual property
        - Confidentiality terms
        - Governing law and jurisdiction
        - Performance obligations
        
        Return ONLY the JSON object, no additional text.
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
            # Try to parse as JSON first
            clauses_data = json.loads(response_text)
            return {
                "status": "success",
                "clauses": clauses_data,
                "total_clauses": len(clauses_data.get("clauses", [])),
                "agent_type": "legal_clause_agent"
            }
        except json.JSONDecodeError:
            # If JSON parsing fails, create structured data from text response
            try:
                structured_clauses = self._parse_text_response(response_text)
                return {
                    "status": "success",
                    "clauses": {"clauses": structured_clauses},
                    "total_clauses": len(structured_clauses),
                    "agent_type": "legal_clause_agent",
                    "parsed_from_text": True
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"Failed to parse clause extraction results: {str(e)}",
                    "raw_response": response_text[:500] + "..." if len(response_text) > 500 else response_text,
                    "agent_type": "legal_clause_agent"
                }

    def _parse_text_response(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse text response into structured clause data"""
        clauses = []
        
        # Common clause types to look for
        clause_types = [
            "payment", "termination", "liability", "confidentiality", 
            "intellectual property", "governing law", "force majeure",
            "amendment", "dispute resolution", "performance"
        ]
        
        # Split response into sections
        sections = response_text.split('\n\n')
        
        for i, section in enumerate(sections):
            if len(section.strip()) > 20:  # Ignore very short sections
                # Try to identify clause type
                clause_type = "General"
                for ctype in clause_types:
                    if ctype.lower() in section.lower():
                        clause_type = ctype.title()
                        break
                
                # Determine importance based on keywords
                importance = "Medium"
                if any(word in section.lower() for word in ["critical", "important", "must", "shall", "liability", "termination"]):
                    importance = "High"
                elif any(word in section.lower() for word in ["may", "optional", "preferred"]):
                    importance = "Low"
                
                clauses.append({
                    "clause_type": clause_type,
                    "content": section.strip()[:200] + "..." if len(section.strip()) > 200 else section.strip(),
                    "importance": importance,
                    "parties_involved": ["Company", "Employee"] if "employ" in section.lower() else ["Party A", "Party B"],
                    "key_terms": self._extract_key_terms(section)
                })
        
        return clauses if clauses else [{
            "clause_type": "General Analysis",
            "content": response_text[:300] + "..." if len(response_text) > 300 else response_text,
            "importance": "Medium",
            "parties_involved": ["Contracting Parties"],
            "key_terms": []
        }]
    
    def _extract_key_terms(self, text: str) -> List[str]:
        """Extract key terms from clause text"""
        import re
        
        # Look for monetary amounts
        money_pattern = r'\$[\d,]+(?:\.\d{2})?'
        money_matches = re.findall(money_pattern, text)
        
        # Look for dates
        date_pattern = r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}'
        date_matches = re.findall(date_pattern, text)
        
        # Look for time periods
        time_pattern = r'\b\d+\s+(?:days?|weeks?|months?|years?)\b'
        time_matches = re.findall(time_pattern, text)
        
        key_terms = money_matches + date_matches + time_matches
        return key_terms[:5]  # Limit to 5 key terms

def create_legal_clause_agent_app():
    """Create the AgentCore app for the Legal Clause Agent"""
    clause_agent = LegalClauseAgent()
    
    @clause_agent.app.entrypoint
    def invoke(payload):
        """AgentCore entrypoint for clause extraction"""
        contract_text = payload.get("contract_text", "")
        if not contract_text:
            return {"error": "No contract text provided"}
        
        result = clause_agent.extract_clauses(contract_text)
        return result
    
    return clause_agent.app

if __name__ == "__main__":
    app = create_legal_clause_agent_app()
    app.run()