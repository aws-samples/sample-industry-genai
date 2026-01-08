#!/usr/bin/env python3
"""
Complete Quick Suite Integration Deployment
Automates: Agent lookup -> Lambda -> Cognito -> API Gateway -> OpenAPI Schema
"""

import os
import sys
import json
import time
import zipfile
import boto3
import subprocess
from botocore.exceptions import ClientError

def get_aws_account_info():
    """Get current AWS account ID and region"""
    try:
        sts_client = boto3.client('sts')
        identity = sts_client.get_caller_identity()
        account_id = identity['Account']
        
        # Get current region from session
        session = boto3.Session()
        region = session.region_name or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        
        return account_id, region
    except Exception as e:
        print(f"❌ Failed to get AWS account info: {e}")
        return None, None

def load_agents_deployment():
    """Load agent deployment information"""
    try:
        with open('deployment/outputs/agents_deployment.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ agents_deployment.json not found")
        print("💡 Run deploy_agents.py first")
        return None

def get_response_agent_arn(deployment_info):
    """Get response_agent ARN from deployment info"""
    agents = deployment_info.get('deployed_agents', {})
    response_agent_arn = agents.get('response_agent')
    
    if not response_agent_arn:
        print("❌ response_agent not found in deployment")
        print("Available agents:", list(agents.keys()))
        return None, None
    
    print(f"✅ Found response_agent: {response_agent_arn}")
    return response_agent_arn, agents

def create_lambda_role(iam_client):
    """Create Lambda execution role"""
    role_name = "QuickSuiteLegalContractLambdaRole"
    
    try:
        # Check if exists
        try:
            role = iam_client.get_role(RoleName=role_name)
            return role['Role']['Arn']
        except iam_client.exceptions.NoSuchEntityException:
            pass
        
        # Create role
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        )
        
        role_arn = response['Role']['Arn']
        
        # Attach policies
        policies = [
            'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
            'arn:aws:iam::aws:policy/AmazonBedrockFullAccess'
        ]
        
        for policy in policies:
            iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        
        # Add AgentCore permissions
        agentcore_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:GetAgentRuntime"
                ],
                "Resource": "*"
            }]
        }
        
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName='BedrockAgentCoreAccess',
            PolicyDocument=json.dumps(agentcore_policy)
        )
        
        print(f"✅ Created Lambda role: {role_arn}")
        time.sleep(15)  # Wait for propagation
        return role_arn
        
    except Exception as e:
        print(f"❌ Failed to create Lambda role: {e}")
        return None

def create_lambda_function(lambda_client, function_name, role_arn, response_agent_arn, all_agent_arns):
    """Create Lambda function using response agent"""
    try:
        # Read the Lambda function code from file
        with open('deployment/quicksuite_lambda.py', 'r') as f:
            lambda_code = f.read()
        
        # Replace placeholders with actual ARNs
        lambda_code = lambda_code.replace('RESPONSE_AGENT_ARN_PLACEHOLDER', response_agent_arn)
        
        # Create zip file
        zip_path = "quicksuite_lambda.zip"
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.writestr("lambda_function.py", lambda_code)
        
        with open(zip_path, 'rb') as f:
            zip_content = f.read()
        
        try:
            # Try to create function
            response = lambda_client.create_function(
                FunctionName=function_name,
                Runtime='python3.11',
                Role=role_arn,
                Handler='lambda_function.lambda_handler',
                Code={'ZipFile': zip_content},
                Timeout=300,
                MemorySize=512,
                Environment={'Variables': {
                    'RESPONSE_AGENT_ARN': response_agent_arn,
                    'ALL_AGENT_ARNS': json.dumps(all_agent_arns)
                }}
            )
            print(f"✅ Created Lambda function: {response['FunctionArn']}")
            
        except lambda_client.exceptions.ResourceConflictException:
            # Function exists, update it
            print("⚠️ Function exists, updating...")
            
            # Wait for any pending updates to complete
            import time
            max_retries = 10
            for i in range(max_retries):
                try:
                    response = lambda_client.update_function_code(
                        FunctionName=function_name,
                        ZipFile=zip_content
                    )
                    break
                except lambda_client.exceptions.ResourceConflictException:
                    if i < max_retries - 1:
                        print(f"⏳ Waiting for function update to complete... ({i+1}/{max_retries})")
                        time.sleep(10)
                    else:
                        raise
            
            # Wait a bit more before updating configuration
            time.sleep(5)
            
            # Update environment variables
            lambda_client.update_function_configuration(
                FunctionName=function_name,
                Environment={'Variables': {
                    'RESPONSE_AGENT_ARN': response_agent_arn,
                    'ALL_AGENT_ARNS': json.dumps(all_agent_arns)
                }}
            )
            print(f"✅ Updated Lambda function: {response['FunctionArn']}")
        
        os.remove(zip_path)
        return response['FunctionArn']
        
    except Exception as e:
        print(f"❌ Failed to create Lambda function: {e}")
        return None

def create_cognito_user_pool(cognito_client, region):
    """Create Cognito User Pool and app client"""
    import time
    
    try:
        # Create User Pool
        pool_response = cognito_client.create_user_pool(
            PoolName='quicksuite-legal-contract-pool',
            AdminCreateUserConfig={'AllowAdminCreateUserOnly': True}
        )
        
        user_pool_id = pool_response['UserPool']['Id']
        print(f"✅ Created User Pool: {user_pool_id}")
        
        # Create domain
        domain_name = f"legal-qs-{int(time.time())}"
        cognito_client.create_user_pool_domain(
            Domain=domain_name,
            UserPoolId=user_pool_id
        )
        
        domain_url = f"https://{domain_name}.auth.{region}.amazoncognito.com"
        print(f"✅ Created domain: {domain_url}")
        
        # Create resource server
        resource_response = cognito_client.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier="legal-contract-api",
            Name="Legal Contract API",
            Scopes=[
                {'ScopeName': 'analyze', 'ScopeDescription': 'Analyze contracts'}
            ]
        )
        
        # Create resource server for custom scopes
        resource_server_id = f'legal-contract-api-{int(time.time())}'
        resource_server_response = cognito_client.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=resource_server_id,
            Name='Legal Contract API',
            Scopes=[{
                'ScopeName': 'analyze',
                'ScopeDescription': 'Analyze legal contracts'
            }]
        )
        
        # Create app client
        client_response = cognito_client.create_user_pool_client(
            UserPoolId=user_pool_id,
            ClientName='quicksuite-client',
            GenerateSecret=True,
            AllowedOAuthFlows=['client_credentials'],
            AllowedOAuthScopes=[f'{resource_server_id}/analyze'],
            AllowedOAuthFlowsUserPoolClient=True
        )
        
        client_id = client_response['UserPoolClient']['ClientId']
        client_secret = client_response['UserPoolClient']['ClientSecret']
        
        cognito_config = {
            'user_pool_id': user_pool_id,
            'domain_url': domain_url,
            'token_url': f"{domain_url}/oauth2/token",
            'client_id': client_id,
            'client_secret': client_secret,
            'scopes': [f'{resource_server_id}/analyze']
        }
        
        print(f"✅ Created Cognito configuration")
        return cognito_config
        
    except Exception as e:
        print(f"❌ Failed to create Cognito User Pool: {e}")
        return None

def create_api_gateway(apigw_client, lambda_client, function_arn, cognito_config, account_id, region):
    """Create API Gateway with Cognito auth"""
    try:
        # Create API
        api_response = apigw_client.create_rest_api(
            name='QuickSuiteLegalContractAPI',
            description='Legal Contract Analysis API for Quick Suite'
        )
        
        api_id = api_response['id']
        print(f"✅ Created API Gateway: {api_id}")
        
        # Create authorizer
        user_pool_arn = f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{cognito_config['user_pool_id']}"
        
        auth_response = apigw_client.create_authorizer(
            restApiId=api_id,
            name='cognito-auth',
            type='COGNITO_USER_POOLS',
            providerARNs=[user_pool_arn],
            identitySource='method.request.header.Authorization'
        )
        
        authorizer_id = auth_response['id']
        
        # Get root resource
        resources = apigw_client.get_resources(restApiId=api_id)
        root_id = resources['items'][0]['id']
        
        # Create /analyze resource
        resource_response = apigw_client.create_resource(
            restApiId=api_id,
            parentId=root_id,
            pathPart='analyze'
        )
        
        resource_id = resource_response['id']
        
        # Create POST method
        apigw_client.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod='POST',
            authorizationType='COGNITO_USER_POOLS',
            authorizerId=authorizer_id,
            authorizationScopes=cognito_config['scopes']
        )
        
        # Lambda integration
        lambda_uri = f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{function_arn}/invocations"
        
        apigw_client.put_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod='POST',
            type='AWS_PROXY',
            integrationHttpMethod='POST',
            uri=lambda_uri
        )
        
        # Lambda permission
        lambda_client.add_permission(
            FunctionName=function_arn.split(':')[-1],
            StatementId=f"apigateway-{api_id}",
            Action='lambda:InvokeFunction',
            Principal='apigateway.amazonaws.com',
            SourceArn=f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*"
        )
        
        # Deploy API
        apigw_client.create_deployment(
            restApiId=api_id,
            stageName='prod'
        )
        
        api_url = f"https://{api_id}.execute-api.{region}.amazonaws.com/prod/analyze"
        
        print(f"✅ Created API Gateway: {api_url}")
        return api_id, api_url
        
    except Exception as e:
        print(f"❌ Failed to create API Gateway: {e}")
        return None, None

def create_openapi_schema(api_url, cognito_config):
    """Create OpenAPI schema for Quick Suite"""
    schema = {
        "openapi": "3.0.0",
        "info": {
            "title": "Legal Contract Analysis API",
            "version": "1.0.0",
            "description": "AI-powered legal contract analysis for Quick Suite"
        },
        "servers": [{"url": api_url.replace('/analyze', '')}],
        "components": {
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": cognito_config['token_url'],
                            "scopes": {
                                scope: f"Access to {scope}" for scope in cognito_config['scopes']
                            }
                        }
                    }
                }
            }
        },
        "security": [{"oauth2": cognito_config['scopes']}],
        "paths": {
            "/analyze": {
                "post": {
                    "summary": "Analyze legal contract",
                    "security": [{"oauth2": cognito_config['scopes']}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "contract_text": {"type": "string"},
                                        "question": {"type": "string"}
                                    },
                                    "required": ["contract_text"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Successful analysis"},
                        "400": {"description": "Invalid input"},
                        "401": {"description": "Unauthorized"},
                        "500": {"description": "Server error"}
                    }
                }
            }
        }
    }
    
    with open('quicksuite_openapi_schema.json', 'w') as f:
        json.dump(schema, f, indent=2)
    
    print("✅ Created OpenAPI schema: quicksuite_openapi_schema.json")
    return schema

def main():
    print("🚀 Quick Suite Integration Deployment")
    print("="*50)
    
    # Get AWS account info
    account_id, region = get_aws_account_info()
    if not account_id or not region:
        print("❌ Could not get AWS account info")
        return 1
    
    print(f"📋 Using AWS Account: {account_id}")
    print(f"🌍 Using Region: {region}")
    
    # Load agent deployment info
    deployment_info = load_agents_deployment()
    if not deployment_info:
        return 1
    
    # Get response agent ARN and all agent ARNs
    response_agent_arn, all_agent_arns = get_response_agent_arn(deployment_info)
    if not response_agent_arn:
        return 1
    
    # Initialize clients with dynamic region
    iam_client = boto3.client('iam', region_name=region)
    lambda_client = boto3.client('lambda', region_name=region)
    cognito_client = boto3.client('cognito-idp', region_name=region)
    apigw_client = boto3.client('apigateway', region_name=region)
    
    function_name = "quicksuite-legal-contract-api"
    
    print("\n🔧 Creating Lambda function...")
    role_arn = create_lambda_role(iam_client)
    if not role_arn:
        return 1
    
    function_arn = create_lambda_function(lambda_client, function_name, role_arn, response_agent_arn, all_agent_arns)
    if not function_arn:
        return 1
    
    print("\n🔧 Creating Lambda function...")
    role_arn = create_lambda_role(iam_client)
    if not role_arn:
        return 1
    
    function_arn = create_lambda_function(lambda_client, function_name, role_arn, response_agent_arn, all_agent_arns)
    if not function_arn:
        return 1
    
    print("\n🔧 Creating Cognito User Pool...")
    cognito_config = create_cognito_user_pool(cognito_client, region)
    if not cognito_config:
        return 1
    
    print("\n🔧 Creating API Gateway...")
    # Get account ID and region from response_agent ARN
    account_id = response_agent_arn.split(':')[4]
    region = response_agent_arn.split(':')[3]
    
    api_id, api_url = create_api_gateway(apigw_client, lambda_client, function_arn, cognito_config, account_id, region)
    if not api_id:
        return 1
    
    print("\n🔧 Creating OpenAPI schema...")
    print("\n📄 Generating OpenAPI schema...")
    openapi_schema = generate_openapi_schema(api_url, cognito_config)
    
    # Save complete configuration
    quicksuite_config = {
        "response_agent_arn": response_agent_arn,
        "all_agent_arns": all_agent_arns,
        "lambda_function_arn": function_arn,
        "api_gateway_id": api_id,
        "api_url": api_url.replace('/analyze', ''),
        "cognito": cognito_config,
        "quicksuite_integration": {
            "client_id": cognito_config['client_id'],
            "client_secret": cognito_config['client_secret'],
            "token_url": cognito_config['token_url'],
            "api_endpoint": api_url.replace('/analyze', '')
        }
    }
    
    with open('quicksuite_integration_config.json', 'w') as f:
        json.dump(quicksuite_config, f, indent=2)
    
    with open('quicksuite_openapi_schema.json', 'w') as f:
        json.dump(openapi_schema, f, indent=2)
    
    print(f"\n{'='*50}")
    print("🎉 QUICK SUITE INTEGRATION READY")
    print(f"{'='*50}")
    print(f"\n📊 Configuration Summary:")
    print(f"   • API Endpoint: {api_url.replace('/analyze', '')}")
    print(f"   • Client ID: {cognito_config['client_id']}")
    print(f"   • Client Secret: {cognito_config['client_secret']}")
    print(f"   • Token URL: {cognito_config['token_url']}")
    
    print(f"\n📄 Files Created:")
    print(f"   • quicksuite_integration_config.json - Complete configuration")
    print(f"   • quicksuite_openapi_schema.json - OpenAPI schema for Quick Suite")
    
    print(f"\n🔧 Quick Suite Setup:")
    print(f"   1. Use the Client ID and Secret above")
    print(f"   2. Import the OpenAPI schema file")
    print(f"   3. Configure OAuth 2.0 client credentials flow")
    
    return 0

def generate_openapi_schema(api_url, cognito_config):
    """Generate OpenAPI schema for Quick Suite integration"""
    account_id, region = get_aws_account_info()
    
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Legal Contract Analysis API",
            "description": "AI-powered legal contract analysis using AWS Bedrock AgentCore",
            "version": "1.0.0"
        },
        "servers": [{"url": api_url.replace('/analyze', '')}],
        "security": [{"CognitoAuth": []}],
        "paths": {
            "/analyze": {
                "post": {
                    "summary": "Analyze legal contract",
                    "description": "Analyze a legal contract using AI agents for clause extraction, risk assessment, and compliance checking",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["contract_text"],
                                    "properties": {
                                        "contract_text": {
                                            "type": "string",
                                            "description": "The legal contract text to analyze"
                                        },
                                        "user_question": {
                                            "type": "string",
                                            "description": "Optional specific question about the contract"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Successful analysis",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "analysis": {
                                                "type": "object",
                                                "properties": {
                                                    "contract_analysis": {
                                                        "type": "object",
                                                        "properties": {
                                                            "clause_analysis": {"type": "object"},
                                                            "risk_analysis": {"type": "object"},
                                                            "compliance_analysis": {"type": "object"},
                                                            "final_response": {"type": "object"}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "400": {
                            "description": "Bad request",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "error": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "securitySchemes": {
                "CognitoAuth": {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": f"https://cognito-idp.{region}.amazonaws.com/{cognito_config['user_pool_id']}/oauth2/token"
                        }
                    }
                }
            }
        }
    }

if __name__ == "__main__":
    sys.exit(main())
