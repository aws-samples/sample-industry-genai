import json
import os
import uuid
import time
from typing import Dict, Any
import boto3
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def generate_session_id() -> str:
    """Generate a random session ID that meets the 33+ character requirement."""
    # Combine UUID and timestamp to ensure uniqueness and meet length requirement
    session_id = f"{uuid.uuid4().hex}{int(time.time())}"
    return session_id

# Lambda handler
def lambda_handler(event, context):
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        
        # Parse API Gateway event
        if 'body' in event:
            # API Gateway request
            body = json.loads(event['body']) if event['body'] else {}
            contract_text = body.get('contract_text')
            user_question = body.get('question') 
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing request body'})
            }
        
        if not contract_text:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'contract_text is required'})
            }
        
        # Process contract
        try:
            orchestrator = LegalContractOrchestrator()
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': f'Configuration error: {str(e)}'})
            }
        
        logger.info("Processing contract...")
        result = orchestrator.process_contract(contract_text, user_question)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'POST,OPTIONS'
            },
            'body': json.dumps(result)
        }
        
    except ClientError as e:
        logger.error(f"AWS ClientError: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'AWS service error', 'details': str(e)})
        }
    except ValueError as e:
        logger.error(f"ValueError: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid input', 'details': str(e)})
        }
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error', 'details': str(e)})
        }

# Simplified orchestrator for Lambda
class LegalContractOrchestrator:
    
    def __init__(self):
        # Use the correct agent runtime ARN directly
        self.agent_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:691595240138:runtime/legalresponseagent-KewJW3DJIk"
        logger.info(f"Using agent runtime ARN: {self.agent_runtime_arn}")
    
    def process_contract(self, contract_text: str, user_question: str = None) -> Dict[str, Any]:
        
        logger.info("Processing contract: inside the function...")
        
        prompt = json.dumps({'contract_text': contract_text, 'user_question': user_question})
        logger.info(f"Prompt: {prompt}")

        try:
            # Use bedrock-agentcore client with correct method
            client = boto3.client('bedrock-agentcore', region_name='us-east-1')
            logger.info("Created bedrock-agentcore client")
            
            # Use invoke_agent_runtime with correct parameters
            response = client.invoke_agent_runtime(
                agentRuntimeArn=self.agent_runtime_arn,
                runtimeSessionId=generate_session_id(),
                payload=prompt,
                qualifier="DEFAULT"
            )
            
            logger.info("Agent invocation successful")
            
            # Process response
            response_body = response['response'].read()
            response_data = json.loads(response_body)
            logger.info(f"Agent response: {response_data}")
            
            return {
                "status": "success",
                "contract_analysis": {
                    "response": response_data,
                    "agent_runtime": self.agent_runtime_arn
                }
            }
            
        except ClientError as e:
            logger.error(f"Bedrock agentcore error: {str(e)}")
            # Return error details for debugging
            return {
                "status": "error",
                "error": str(e),
                "contract_analysis": {
                    "message": "Failed to invoke agent runtime",
                    "agent_runtime": self.agent_runtime_arn
                }
            }
        except Exception as e:
            logger.error(f"Unexpected error in process_contract: {str(e)}")
            raise
