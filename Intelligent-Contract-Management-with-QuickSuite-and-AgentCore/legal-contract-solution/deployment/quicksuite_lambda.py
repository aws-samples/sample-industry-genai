import json
import boto3
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def generate_session_id():
    import uuid
    return str(uuid.uuid4())


def invoke_agent(client, agent_arn, payload):
    """Invoke a single agent runtime and return parsed response."""
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=generate_session_id(),
            payload=json.dumps(payload),
            qualifier="DEFAULT"
        )
        response_body = response['response'].read()
        return json.loads(response_body)
    except Exception as e:
        logger.error(f"Failed to invoke agent {agent_arn}: {str(e)}")
        return {"status": "error", "message": str(e)}


def lambda_handler(event, context):
    """Lambda handler - orchestrates multi-agent contract analysis.

    Invokes clause, risk, and compliance agents IN PARALLEL,
    then passes all results to the response agent for synthesis.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        contract_text = body.get('contract_text', '')

        # Accept either field name. The OpenAPI schema exposed to Quick Suite uses
        # "user_question"; "question" is accepted for backward compatibility with
        # earlier schema versions that declared that name.
        user_question = body.get('user_question') or body.get('question') or ''

        if not contract_text:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
                },
                'body': json.dumps({'error': 'contract_text is required'})
            }

        # Get agent ARNs from environment
        response_agent_arn = os.environ.get('RESPONSE_AGENT_ARN', '')
        all_agent_arns = json.loads(os.environ.get('ALL_AGENT_ARNS', '{}'))

        # Initialize AgentCore client
        session = boto3.Session()
        region = session.region_name or 'us-east-1'
        client = boto3.client('bedrock-agentcore', region_name=region)

        base_payload = {'contract_text': contract_text}

        # --- PARALLEL INVOCATION of analysis agents ---
        clause_result = None
        risk_result = None
        compliance_result = None

        clause_arn = all_agent_arns.get('legal_clause_agent')
        risk_arn = all_agent_arns.get('legal_risk_agent')
        compliance_arn = all_agent_arns.get('compliance_agent')

        futures = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            if clause_arn:
                futures[executor.submit(invoke_agent, client, clause_arn, base_payload)] = 'clause'
            if risk_arn:
                futures[executor.submit(invoke_agent, client, risk_arn, base_payload)] = 'risk'
            if compliance_arn:
                futures[executor.submit(invoke_agent, client, compliance_arn, base_payload)] = 'compliance'

            for future in as_completed(futures):
                agent_name = futures[future]
                try:
                    result = future.result()
                    logger.info(f"{agent_name} agent status: {result.get('status')}")
                    if agent_name == 'clause':
                        clause_result = result
                    elif agent_name == 'risk':
                        risk_result = result
                    elif agent_name == 'compliance':
                        compliance_result = result
                except Exception as e:
                    logger.error(f"{agent_name} agent exception: {str(e)}")

        # --- SEQUENTIAL: Pass all results to response agent for synthesis ---
        response_payload = {
            'clause_analysis': clause_result,
            'risk_analysis': risk_result,
            'compliance_analysis': compliance_result,
            'user_question': user_question
        }

        logger.info("Invoking response agent for synthesis...")
        final_result = invoke_agent(client, response_agent_arn, response_payload)
        logger.info(f"Response agent status: {final_result.get('status')}")

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
                'contract_analysis': final_result,
                'agent_results': {
                    'clause_agent': clause_result.get('status') if clause_result else 'skipped',
                    'risk_agent': risk_result.get('status') if risk_result else 'skipped',
                    'compliance_agent': compliance_result.get('status') if compliance_result else 'skipped',
                    'response_agent': final_result.get('status')
                },
                'agent_runtime': response_agent_arn
            })
        }

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'status': 'error',
                'error': str(e),
                'contract_analysis': {'message': 'Failed to process contract'}
            })
        }
