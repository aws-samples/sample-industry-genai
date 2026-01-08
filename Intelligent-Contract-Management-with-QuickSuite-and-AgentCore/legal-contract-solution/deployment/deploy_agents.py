#!/usr/bin/env python3
"""
Modern AgentCore CLI deployment for all four legal contract agents
"""
import subprocess
import os
import sys
import json
import re
import boto3

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
        sys.exit(1)

def create_ecr_repository():
    """Create ECR repository if it doesn't exist"""
    try:
        account_id, region = get_aws_account_info()
        ecr_client = boto3.client('ecr', region_name=region)
        repo_name = 'legal-contract-agentcore'
        
        try:
            # Check if repository exists
            ecr_client.describe_repositories(repositoryNames=[repo_name])
            print(f"✅ ECR repository already exists: {repo_name}")
        except ecr_client.exceptions.RepositoryNotFoundException:
            # Create repository
            response = ecr_client.create_repository(repositoryName=repo_name)
            repo_uri = response['repository']['repositoryUri']
            print(f"✅ Created ECR repository: {repo_uri}")
            
    except Exception as e:
        print(f"❌ Failed to create ECR repository: {e}")
        return False
    
    return True

def create_agent_config(agent_name, entrypoint):
    """Create .bedrock_agentcore.yaml for specific agent"""
    account_id, region = get_aws_account_info()
    
    config = f"""default_agent: {agent_name}
agents:
  {agent_name}:
    name: {agent_name}
    entrypoint: {entrypoint}
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
    
    with open(".bedrock_agentcore.yaml", "w") as f:
        f.write(config)
    print(f"✅ Created config for {agent_name}")

def extract_agent_arn(output_text):
    """Extract agent ARN from agentcore deploy output"""
    # Look for ARN patterns in the output
    arn_pattern = r'arn:aws:bedrock-agentcore:[^:]+:[^:]+:runtime/[^\s]+'
    matches = re.findall(arn_pattern, output_text)
    return matches[0] if matches else None

def deploy_agent(agent_name, entrypoint):
    """Deploy single agent using agentcore deploy"""
    print(f"\n🚀 Deploying {agent_name}...")
    
    # Create config
    create_agent_config(agent_name, entrypoint)
    
    try:
        result = subprocess.run([
            "agentcore", "deploy"
        ], timeout=300, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✅ {agent_name} deployed successfully")
            # Extract ARN from output
            agent_arn = extract_agent_arn(result.stdout)
            if agent_arn:
                print(f"📋 Agent ARN: {agent_arn}")
            return True, agent_arn
        else:
            print(f"❌ {agent_name} failed: {result.stderr}")
            return False, None
            
    except subprocess.TimeoutExpired:
        print(f"❌ {agent_name} timed out")
        return False, None
    except Exception as e:
        print(f"❌ {agent_name} error: {e}")
        return False, None

def save_deployment_info(deployed_agents):
    """Save agent deployment information to JSON file"""
    deployment_info = {
        "deployed_agents": deployed_agents,
        "deployment_timestamp": subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], 
                                             capture_output=True, text=True).stdout.strip(),
        "total_agents": len(deployed_agents)
    }
    
    # Ensure outputs directory exists
    os.makedirs("deployment/outputs", exist_ok=True)
    
    with open("deployment/outputs/agents_deployment.json", "w") as f:
        json.dump(deployment_info, f, indent=2)
    
    print(f"💾 Saved deployment info to deployment/outputs/agents_deployment.json")

def main():
    """Deploy all four agents"""
    print("🤖 Deploying Legal Contract AI Agents")
    
    # Step 1: Create ECR repository
    print("\n🔧 Setting up ECR repository...")
    if not create_ecr_repository():
        print("❌ Failed to set up ECR repository")
        sys.exit(1)
    
    # Step 2: Deploy agents
    agents = [
        ("legal_clause_agent", "agents/legal_clause_agent.py"),
        ("legal_risk_agent", "agents/legal_risk_agent.py"),
        ("compliance_agent", "agents/compliance_agent.py"),
        ("response_agent", "agents/response_agent.py")
    ]
    
    deployed_agents = {}
    success_count = 0
    
    for name, entrypoint in agents:
        success, arn = deploy_agent(name, entrypoint)
        if success:
            success_count += 1
            if arn:
                deployed_agents[name] = arn
    
    print(f"\n📊 Results: {success_count}/{len(agents)} agents deployed successfully")
    
    if success_count > 0:
        save_deployment_info(deployed_agents)
    
    if success_count == len(agents):
        print("🎉 All agents deployed!")
    else:
        print("⚠️ Some deployments failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
