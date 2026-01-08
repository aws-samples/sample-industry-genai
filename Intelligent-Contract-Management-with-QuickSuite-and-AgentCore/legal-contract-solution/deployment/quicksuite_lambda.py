import json
import boto3
import logging
import os
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def generate_session_id():
    """Generate a unique session ID"""
    import uuid
    return str(uuid.uuid4())

def lambda_handler(event, context):
    """Lambda handler for Quick Suite integration"""
    
    logger.info(f"Received event: {json.dumps(event)}")
    
    try:
        # Parse request body
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event
        
        contract_text = body.get('contract_text', '')
        user_question = body.get('user_question', '')
        
        if not contract_text:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
                },
                'body': json.dumps({
                    'error': 'contract_text is required'
                })
            }
        
        # Get response agent ARN from environment
        response_agent_arn = "RESPONSE_AGENT_ARN_PLACEHOLDER"
        
        # Prepare payload for response agent (orchestrator)
        payload = {
            'contract_text': contract_text,
            'user_question': user_question
        }
        
        logger.info(f"Invoking response agent with payload: {json.dumps(payload)}")
        
        # Invoke response agent
        session = boto3.Session()
        region = session.region_name or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        client = boto3.client('bedrock-agentcore', region_name=region)
        
        response = client.invoke_agent_runtime(
            agentRuntimeArn=response_agent_arn,
            runtimeSessionId=generate_session_id(),
            payload=json.dumps(payload),
            qualifier="DEFAULT"
        )
        
        # Process response
        response_body = response['response'].read()
        response_data = json.loads(response_body)
        
        logger.info(f"Agent response: {json.dumps(response_data)}")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Authorization'
            },
            'body': json.dumps({
                'status': 'success',
                'analysis': response_data,
                'response_agent': response_agent_arn
            })
        }
        
    except ClientError as e:
        logger.error(f"Bedrock agentcore error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': 'Failed to invoke agent runtime',
                'details': str(e)
            })
        }
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': 'Internal server error',
                'details': str(e)
            })
        }
