import os
from dotenv import load_dotenv

load_dotenv()

# AWS Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

# Bedrock Model Configuration with fallbacks
# Try Claude 3 Haiku first (most commonly available), then Sonnet, then other models
BEDROCK_MODEL_FALLBACKS = [
    'anthropic.claude-3-haiku-20240307-v1:0',           # Most accessible
    'anthropic.claude-3-sonnet-20240229-v1:0',         # Good balance
    'anthropic.claude-3-5-sonnet-20240620-v1:0',       # Latest Sonnet
    'us.anthropic.claude-3-5-sonnet-20241022-v2:0',    # Cross-region Sonnet
    'anthropic.claude-v2:1',                            # Legacy fallback
    'anthropic.claude-v2',                              # Legacy fallback
]

# Primary model (can be overridden by environment variable)
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', BEDROCK_MODEL_FALLBACKS[0])

# Agent Configuration
AGENT_RUNTIME_ENDPOINT = os.getenv('AGENT_RUNTIME_ENDPOINT', 'localhost:8080')

# Legal Contract Processing Configuration
def _safe_float(value, default):
    """Safely convert environment variable to float with validation"""
    try:
        result = float(value)
        # Validate range for risk threshold
        if 0.0 <= result <= 1.0:
            return result
        else:
            print(f"⚠️  Invalid RISK_THRESHOLD value {result}, using default {default}")
            return default
    except (ValueError, TypeError):
        print(f"⚠️  Invalid RISK_THRESHOLD format, using default {default}")
        return default

RISK_THRESHOLD = _safe_float(os.getenv('RISK_THRESHOLD', '0.7'), 0.7)
COMPLIANCE_POLICIES = [
    "Data Privacy Requirements",
    "Financial Regulations",
    "Employment Law Compliance",
    "Intellectual Property Protection"
]

def get_available_model():
    """
    Try to find an available Claude model from the fallback list
    Returns the first working model ID
    """
    import boto3
    import json
    
    try:
        bedrock_runtime = boto3.client('bedrock-runtime', region_name=AWS_REGION)
        
        for model_id in BEDROCK_MODEL_FALLBACKS:
            try:
                # Test with a simple message
                test_message = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "Hi"}]
                }
                
                response = bedrock_runtime.invoke_model(
                    modelId=model_id,
                    body=json.dumps(test_message)
                )
                
                # If we get here, the model works
                print(f"✅ Using model: {model_id}")
                return model_id
                
            except Exception as e:
                if "AccessDeniedException" in str(e):
                    continue  # Try next model
                else:
                    # Other error, might be worth reporting
                    print(f"⚠️  Model {model_id} error: {str(e)}")
                    continue
        
        # If no models work, return the default and let it fail with a clear error
        print(f"❌ No accessible Claude models found. Using default: {BEDROCK_MODEL_ID}")
        return BEDROCK_MODEL_ID
        
    except Exception as e:
        print(f"⚠️  Could not test models: {str(e)}")
        return BEDROCK_MODEL_ID