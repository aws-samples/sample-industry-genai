#!/usr/bin/env python3
"""
Comprehensive cleanup script to remove all deployed AWS resources
Removes: AgentCore agents, CodeBuild projects, API Gateway, Lambda, Cognito, IAM roles
"""
import boto3
import json
import subprocess
import time
import os

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

def load_deployment_config():
    """Load deployment configuration"""
    try:
        with open('quicksuite_integration_config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("⚠️ No deployment config found")
        return None

def load_agents_config():
    """Load agents deployment configuration"""
    try:
        with open('deployment/outputs/agents_deployment.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("⚠️ No agents config found")
        return None

def cleanup_agentcore_agents(agents_config):
    """Remove AgentCore agents using agentcore CLI"""
    if not agents_config or 'deployed_agents' not in agents_config:
        print("⚠️ No agents config found")
        return
    
    account_id, region = get_aws_account_info()
    if not account_id or not region:
        print("❌ Could not get AWS account info")
        return
    
    for agent_name, agent_arn in agents_config['deployed_agents'].items():
        try:
            print(f"🗑️ Destroying agent: {agent_name}")
            
            # Extract agent ID from ARN
            agent_id = agent_arn.split('/')[-1]
            
            # Create .bedrock_agentcore.yaml for this agent
            config = f"""default_agent: {agent_name}
agents:
  {agent_name}:
    name: {agent_name}
    entrypoint: agents/{agent_name}.py
    deployment_type: container
    runtime_type: PYTHON_3_10
    platform: linux/arm64
    container_runtime: null
    source_path: .
    aws:
      execution_role_auto_create: true
      account: '{account_id}'
      region: {region}
      ecr_repository: {account_id}.dkr.ecr.{region}.amazonaws.com/legal-contract-agentcore
      ecr_auto_create: false
      s3_path: null
      s3_auto_create: true
      network_configuration:
        network_mode: PUBLIC
        network_mode_config: null
      protocol_configuration:
        server_protocol: HTTP
      observability:
        enabled: true
      lifecycle_configuration:
        idle_runtime_session_timeout: null
        max_lifetime: null
    bedrock_agentcore:
      agent_id: {agent_id}
      agent_arn: {agent_arn}
      agent_session_id: null
    codebuild:
      project_name: bedrock-agentcore-{agent_name}-builder
      source_bucket: bedrock-agentcore-codebuild-sources-{account_id}-{region}
    memory:
      mode: NO_MEMORY
      memory_id: null
      memory_arn: null
      memory_name: null
      event_expiry_days: 30
      first_invoke_memory_check_done: false
      was_created_by_toolkit: false
    identity:
      credential_providers: []
      workload: null
    aws_jwt:
      enabled: false
      audiences: []
      signing_algorithm: ES384
      issuer_url: null
      duration_seconds: 300
    authorizer_configuration: null
    request_header_configuration: null
    oauth_configuration: null
    api_key_env_var_name: null
    api_key_credential_provider_name: null
    is_generated_by_agentcore_create: false
"""
            
            with open('.bedrock_agentcore.yaml', 'w') as f:
                f.write(config)
            
            # Use agentcore CLI to destroy agent (auto-confirm with echo)
            result = subprocess.run(
                f'echo "y" | agentcore destroy --agent {agent_name}',
                shell=True, capture_output=True, text=True, timeout=180
            )
            
            if result.returncode == 0:
                print(f"✅ Destroyed agent: {agent_name}")
            else:
                print(f"❌ Failed to destroy agent {agent_name}: {result.stderr}")
                
        except Exception as e:
            print(f"❌ Error destroying agent {agent_name}: {e}")

def cleanup_api_gateway(config):
    """Remove API Gateway"""
    if not config or 'api_gateway_id' not in config:
        print("⚠️ No API Gateway ID found")
        return
    
    try:
        apigw_client = boto3.client('apigateway')
        api_id = config['api_gateway_id']
        
        apigw_client.delete_rest_api(restApiId=api_id)
        print(f"✅ Deleted API Gateway: {api_id}")
    except Exception as e:
        print(f"❌ Failed to delete API Gateway: {e}")

def cleanup_lambda_function(config):
    """Remove Lambda function"""
    if not config or 'lambda_function_arn' not in config:
        print("⚠️ No Lambda function ARN found")
        return
    
    try:
        lambda_client = boto3.client('lambda')
        # Extract function name from ARN
        function_arn = config['lambda_function_arn']
        function_name = function_arn.split(':')[-1]
        
        lambda_client.delete_function(FunctionName=function_name)
        print(f"✅ Deleted Lambda function: {function_name}")
    except Exception as e:
        print(f"❌ Failed to delete Lambda function: {e}")

def cleanup_cognito_user_pool(config):
    """Remove Cognito User Pool and domain"""
    if not config or 'cognito' not in config:
        print("⚠️ No Cognito config found")
        return
    
    try:
        cognito_client = boto3.client('cognito-idp')
        cognito_config = config['cognito']
        user_pool_id = cognito_config['user_pool_id']
        
        # Extract domain name from domain URL
        domain_url = cognito_config['domain_url']
        domain_name = domain_url.split('//')[1].split('.')[0]
        
        # Delete domain first
        try:
            cognito_client.delete_user_pool_domain(
                Domain=domain_name,
                UserPoolId=user_pool_id
            )
            print(f"✅ Deleted Cognito domain: {domain_name}")
            time.sleep(10)  # Wait for domain deletion
        except Exception as e:
            print(f"⚠️ Domain deletion: {e}")
        
        # Delete user pool
        cognito_client.delete_user_pool(UserPoolId=user_pool_id)
        print(f"✅ Deleted Cognito User Pool: {user_pool_id}")
    except Exception as e:
        print(f"❌ Failed to delete Cognito User Pool: {e}")
        print(f"❌ Failed to delete Cognito User Pool: {e}")

def cleanup_iam_role():
    """Remove IAM role"""
    try:
        iam_client = boto3.client('iam')
        role_name = "QuickSuiteLegalContractLambdaRole"
        
        # Detach policies
        try:
            iam_client.detach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
            )
            iam_client.detach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/AmazonBedrockFullAccess'
            )
        except:
            pass
        
        # Delete inline policy
        try:
            iam_client.delete_role_policy(
                RoleName=role_name,
                PolicyName='BedrockAgentCoreAccess'
            )
        except:
            pass
        
        # Delete role
        iam_client.delete_role(RoleName=role_name)
        print(f"✅ Deleted IAM role: {role_name}")
    except Exception as e:
        print(f"❌ Failed to delete IAM role: {e}")

def cleanup_output_files():
    """Remove generated output files"""
    files_to_remove = [
        'deployment/outputs/agents_deployment.json',
        'quicksuite_integration_config.json', 
        'quicksuite_openapi_schema.json',
        '.bedrock_agentcore.yaml'
    ]
    
    for file_path in files_to_remove:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"✅ Removed: {file_path}")
        except Exception as e:
            print(f"❌ Failed to remove {file_path}: {e}")

def main():
    print("🧹 Starting comprehensive cleanup of deployed resources...")
    print("="*60)
    print("This will remove:")
    print("• AgentCore agents and CodeBuild projects")
    print("• API Gateway endpoint")
    print("• Lambda function")
    print("• Cognito User Pool and domain")
    print("• IAM roles")
    print("• Generated output files")
    print("="*60)
    
    # Load configurations
    config = load_deployment_config()
    agents_config = load_agents_config()
    
    # Cleanup in proper order
    print("\n🗑️ Step 1: Cleaning up API Gateway...")
    cleanup_api_gateway(config)
    
    print("\n🗑️ Step 2: Cleaning up Lambda function...")
    cleanup_lambda_function(config)
    
    print("\n🗑️ Step 3: Cleaning up Cognito User Pool...")
    cleanup_cognito_user_pool(config)
    
    print("\n🗑️ Step 4: Cleaning up IAM role...")
    cleanup_iam_role()
    
    print("\n🗑️ Step 5: Cleaning up AgentCore agents and CodeBuild projects...")
    cleanup_agentcore_agents(agents_config)
    
    print("\n🗑️ Step 6: Cleaning up output files...")
    cleanup_output_files()
    
    print("\n" + "="*60)
    print("🎉 Cleanup completed!")
    print("All deployed resources have been removed.")
    print("• AgentCore agents destroyed with CodeBuild projects")
    print("• AWS resources (API Gateway, Lambda, Cognito) deleted")
    print("• IAM roles and policies cleaned up")
    print("• Local output files removed")
    print("="*60)

if __name__ == "__main__":
    main()
