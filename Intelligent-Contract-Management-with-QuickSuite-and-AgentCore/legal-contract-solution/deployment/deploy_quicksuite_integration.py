#!/usr/bin/env python3
"""Quick Suite integration deployment.

Provisions the synchronous entry point that Amazon Quick Suite calls:

    Quick Suite -> API Gateway (Cognito OAuth) -> Lambda -> AgentCore agents

Agent runtimes themselves are deployed separately with `agentcore deploy`; this
script reads their ARNs from the AgentCore CLI's deployed state and wires the
Lambda, Cognito user pool, API Gateway, and OpenAPI schema around them.

Run from the legal-contract-solution directory:

    python deployment/deploy_quicksuite_integration.py
"""

import os
import sys
import json
import time
import zipfile

import boto3

FUNCTION_NAME = "legal-contract-api"
LAMBDA_ROLE_NAME = "QuickSuiteLegalContractLambdaRole"
USER_POOL_NAME = "quicksuite-legal-contract-pool"
API_NAME = "QuickSuiteLegalContractAPI"
DEPLOYED_STATE_PATH = "agentcore/.cli/deployed-state.json"

# The response agent orchestrates nothing on its own; the Lambda fans out to the
# three analysis agents in parallel and then calls the response agent to synthesize.
ANALYSIS_AGENTS = ("legal_clause_agent", "legal_risk_agent", "compliance_agent")


def get_aws_account_info():
    """Return (account_id, region) for the current credentials."""
    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        region = boto3.Session().region_name or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        return account_id, region
    except Exception as e:
        print(f"❌ Failed to get AWS account info: {e}")
        return None, None


def load_agent_arns(target="default"):
    """Read agent runtime ARNs from the AgentCore CLI deployed state.

    Returns (response_agent_arn, {agent_name: arn}) or (None, None).
    """
    try:
        with open(DEPLOYED_STATE_PATH) as f:
            state = json.load(f)
    except FileNotFoundError:
        print(f"❌ {DEPLOYED_STATE_PATH} not found")
        print("💡 Deploy the agents first:  agentcore deploy")
        print("💡 Run this script from the legal-contract-solution directory.")
        return None, None
    except json.JSONDecodeError as e:
        print(f"❌ {DEPLOYED_STATE_PATH} is not valid JSON: {e}")
        return None, None

    runtimes = (
        state.get("targets", {})
        .get(target, {})
        .get("resources", {})
        .get("runtimes", {})
    )
    if not runtimes:
        print(f"❌ No agent runtimes found for target '{target}' in {DEPLOYED_STATE_PATH}")
        return None, None

    arns = {name: info.get("runtimeArn") for name, info in runtimes.items()}

    response_agent_arn = arns.get("response_agent")
    if not response_agent_arn:
        print("❌ response_agent not found in deployed state")
        print("   Available agents:", ", ".join(sorted(arns)))
        return None, None

    missing = [a for a in ANALYSIS_AGENTS if not arns.get(a)]
    if missing:
        print(f"⚠️  Analysis agents missing from deployed state: {', '.join(missing)}")
        print("   The Lambda will skip them and the report will be less complete.")

    print(f"✅ Found {len(arns)} agent runtimes (target: {target})")
    for name in sorted(arns):
        print(f"   • {name}")

    return response_agent_arn, arns


def create_lambda_role(iam_client):
    """Create or reuse the Lambda execution role."""
    try:
        role = iam_client.get_role(RoleName=LAMBDA_ROLE_NAME)
        print(f"✅ Reusing Lambda role: {LAMBDA_ROLE_NAME}")
        return role["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        pass

    try:
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }
        role_arn = iam_client.create_role(
            RoleName=LAMBDA_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for the Quick Suite legal contract API Lambda",
        )["Role"]["Arn"]

        iam_client.attach_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )

        # Scoped to the AgentCore invoke actions the Lambda actually needs.
        iam_client.put_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyName="BedrockAgentCoreAccess",
            PolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:InvokeAgentRuntime",
                        "bedrock-agentcore:GetAgentRuntime",
                    ],
                    "Resource": "*",
                }],
            }),
        )

        print(f"✅ Created Lambda role: {role_arn}")
        print("⏳ Waiting 15s for IAM propagation...")
        time.sleep(15)
        return role_arn

    except Exception as e:
        print(f"❌ Failed to create Lambda role: {e}")
        return None


def deploy_lambda(lambda_client, role_arn, response_agent_arn, all_agent_arns):
    """Create or update the orchestrating Lambda function."""
    source = "deployment/quicksuite_lambda.py"
    try:
        with open(source) as f:
            lambda_code = f.read()
    except FileNotFoundError:
        print(f"❌ {source} not found. Run from the legal-contract-solution directory.")
        return None

    env_vars = {
        "RESPONSE_AGENT_ARN": response_agent_arn,
        "ALL_AGENT_ARNS": json.dumps(
            {k: v for k, v in all_agent_arns.items() if k in ANALYSIS_AGENTS}
        ),
    }

    zip_path = "quicksuite_lambda.zip"
    try:
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("lambda_function.py", lambda_code)
        with open(zip_path, "rb") as f:
            zip_content = f.read()

        try:
            response = lambda_client.create_function(
                FunctionName=FUNCTION_NAME,
                Runtime="python3.12",
                Role=role_arn,
                Handler="lambda_function.lambda_handler",
                Code={"ZipFile": zip_content},
                # Generous relative to the ~20s observed runtime; API Gateway's own
                # 29s integration timeout is the binding constraint, not this.
                Timeout=300,
                MemorySize=512,
                Environment={"Variables": env_vars},
                Description="Orchestrates AgentCore legal contract analysis agents",
            )
            print(f"✅ Created Lambda function: {response['FunctionArn']}")
            return response["FunctionArn"]

        except lambda_client.exceptions.ResourceConflictException:
            print("⚠️  Function exists, updating code and configuration...")

        # Update path: code first, then configuration, waiting out in-progress updates.
        response = _retry_on_conflict(
            lambda_client,
            lambda: lambda_client.update_function_code(
                FunctionName=FUNCTION_NAME, ZipFile=zip_content
            ),
            "code update",
        )
        if response is None:
            return None

        _retry_on_conflict(
            lambda_client,
            lambda: lambda_client.update_function_configuration(
                FunctionName=FUNCTION_NAME, Environment={"Variables": env_vars}
            ),
            "configuration update",
        )

        print(f"✅ Updated Lambda function: {response['FunctionArn']}")
        return response["FunctionArn"]

    except Exception as e:
        print(f"❌ Failed to deploy Lambda function: {e}")
        return None
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)


def _retry_on_conflict(lambda_client, fn, label, max_retries=10, delay=8):
    """Lambda rejects concurrent updates; retry while one is in flight."""
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except lambda_client.exceptions.ResourceConflictException:
            if attempt == max_retries:
                print(f"❌ {label} still conflicting after {max_retries} attempts")
                return None
            print(f"⏳ Waiting for in-flight update before {label} ({attempt}/{max_retries})...")
            time.sleep(delay)
    return None


def find_existing_user_pool(cognito_client):
    """Return the id of an existing pool with our name, if any."""
    paginator = cognito_client.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool.get("Name") == USER_POOL_NAME:
                return pool["Id"]
    return None


def create_cognito_user_pool(cognito_client, region):
    """Create a Cognito user pool, resource server, domain, and app client."""
    existing = find_existing_user_pool(cognito_client)
    if existing:
        print(f"❌ A user pool named '{USER_POOL_NAME}' already exists ({existing}).")
        print("   Re-running would create duplicate pools with new client secrets.")
        print("💡 Either reuse the values in quicksuite_integration_config.json, or")
        print("   remove the existing resources first:  python deployment/cleanup.py")
        return None

    try:
        user_pool_id = cognito_client.create_user_pool(
            PoolName=USER_POOL_NAME,
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        )["UserPool"]["Id"]
        print(f"✅ Created user pool: {user_pool_id}")

        # Domain must be globally unique; the suffix keeps re-runs from colliding.
        domain_name = f"legal-qs-{int(time.time())}"
        cognito_client.create_user_pool_domain(Domain=domain_name, UserPoolId=user_pool_id)
        domain_url = f"https://{domain_name}.auth.{region}.amazoncognito.com"
        print(f"✅ Created domain: {domain_url}")

        # One resource server defining the single scope the API authorizes against.
        resource_server_id = "legal-contract-api"
        cognito_client.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=resource_server_id,
            Name="Legal Contract API",
            Scopes=[{"ScopeName": "analyze", "ScopeDescription": "Analyze legal contracts"}],
        )
        scope = f"{resource_server_id}/analyze"
        print(f"✅ Created resource server scope: {scope}")

        client = cognito_client.create_user_pool_client(
            UserPoolId=user_pool_id,
            ClientName="quicksuite-client",
            GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthScopes=[scope],
            AllowedOAuthFlowsUserPoolClient=True,
        )["UserPoolClient"]

        return {
            "user_pool_id": user_pool_id,
            "domain_url": domain_url,
            # Token endpoint lives on the pool's hosted domain, NOT on
            # cognito-idp.<region>.amazonaws.com.
            "token_url": f"{domain_url}/oauth2/token",
            "client_id": client["ClientId"],
            "client_secret": client["ClientSecret"],
            "scopes": [scope],
        }

    except Exception as e:
        print(f"❌ Failed to create Cognito user pool: {e}")
        return None


def create_api_gateway(apigw_client, lambda_client, function_arn, cognito_config,
                       account_id, region):
    """Create the REST API, Cognito authorizer, and Lambda proxy integration."""
    try:
        api_id = apigw_client.create_rest_api(
            name=API_NAME,
            description="Legal Contract Analysis API for Quick Suite",
        )["id"]
        print(f"✅ Created API Gateway: {api_id}")

        user_pool_arn = (
            f"arn:aws:cognito-idp:{region}:{account_id}:"
            f"userpool/{cognito_config['user_pool_id']}"
        )
        authorizer_id = apigw_client.create_authorizer(
            restApiId=api_id,
            name="cognito-auth",
            type="COGNITO_USER_POOLS",
            providerARNs=[user_pool_arn],
            identitySource="method.request.header.Authorization",
        )["id"]

        root_id = apigw_client.get_resources(restApiId=api_id)["items"][0]["id"]
        resource_id = apigw_client.create_resource(
            restApiId=api_id, parentId=root_id, pathPart="analyze"
        )["id"]

        apigw_client.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="POST",
            authorizationType="COGNITO_USER_POOLS",
            authorizerId=authorizer_id,
            authorizationScopes=cognito_config["scopes"],
        )

        apigw_client.put_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="POST",
            type="AWS_PROXY",
            integrationHttpMethod="POST",
            uri=(
                f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/"
                f"functions/{function_arn}/invocations"
            ),
            # 29s is the documented maximum for REST APIs and cannot be raised.
            timeoutInMillis=29000,
        )

        lambda_client.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId=f"apigateway-{api_id}",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*",
        )

        apigw_client.create_deployment(restApiId=api_id, stageName="prod")

        base_url = f"https://{api_id}.execute-api.{region}.amazonaws.com/prod"
        print(f"✅ Deployed API: {base_url}/analyze")
        return api_id, base_url

    except Exception as e:
        print(f"❌ Failed to create API Gateway: {e}")
        return None, None


def generate_openapi_schema(base_url, cognito_config):
    """Build the OpenAPI schema Quick Suite imports.

    Mirrors ui/quick-suite/api_gateway_openapi_schema.json with live values.
    Quick Suite constraints honored here: an operationId is required for actions
    to be generated, array-typed properties are unsupported, and every operation
    and property needs a description.
    """
    scopes = cognito_config["scopes"]
    scope_map = {s: "Analyze legal contracts" for s in scopes}

    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Legal Contract Analysis API",
            "description": (
                "AI-powered legal contract analysis. Orchestrates four Amazon Bedrock "
                "AgentCore agents to extract clauses, assess risk, check regulatory "
                "compliance, and synthesize an executive report."
            ),
            "version": "2.0.0",
        },
        "servers": [{"url": base_url}],
        "components": {
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": cognito_config["token_url"],
                            "scopes": scope_map,
                        }
                    },
                }
            }
        },
        "security": [{"oauth2": scopes}],
        "paths": {
            "/analyze": {
                "post": {
                    "operationId": "analyzeContract",
                    "summary": "Analyze a legal contract",
                    "description": (
                        "Submit the full text of a legal contract for AI-powered analysis. "
                        "Returns an executive report covering key clauses, a risk assessment "
                        "with an overall risk score, regulatory compliance status, recommended "
                        "actions, and an approve/review/reject recommendation. Optionally "
                        "accepts a specific question to answer about the contract instead of "
                        "producing a general summary."
                    ),
                    "security": [{"oauth2": scopes}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "description": (
                                        "The contract to analyze and an optional question "
                                        "about it."
                                    ),
                                    "required": ["contract_text"],
                                    "properties": {
                                        "contract_text": {
                                            "type": "string",
                                            "description": (
                                                "The full plain text of the legal contract "
                                                "to analyze. Required."
                                            ),
                                        },
                                        "user_question": {
                                            "type": "string",
                                            "description": (
                                                "Optional specific question to answer about "
                                                "the contract, for example 'Is our liability "
                                                "actually capped?'. When omitted, a full "
                                                "executive summary is returned instead of a "
                                                "targeted answer."
                                            ),
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Successful contract analysis",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "description": (
                                            "The analysis report plus per-agent execution "
                                            "status."
                                        ),
                                        "properties": {
                                            "status": {
                                                "type": "string",
                                                "description": (
                                                    "Overall outcome of the request, either "
                                                    "success or error."
                                                ),
                                            },
                                            "contract_analysis": {
                                                "type": "object",
                                                "description": (
                                                    "The synthesized analysis produced by the "
                                                    "response agent."
                                                ),
                                                "properties": {
                                                    "status": {
                                                        "type": "string",
                                                        "description": (
                                                            "Outcome reported by the response "
                                                            "agent."
                                                        ),
                                                    },
                                                    "summary_response": {
                                                        "type": "string",
                                                        "description": (
                                                            "The formatted analysis report in "
                                                            "Markdown."
                                                        ),
                                                    },
                                                    "agent_type": {
                                                        "type": "string",
                                                        "description": (
                                                            "Identifier of the agent that "
                                                            "produced the report."
                                                        ),
                                                    },
                                                    "includes_user_question": {
                                                        "type": "boolean",
                                                        "description": (
                                                            "True when the report answers a "
                                                            "specific supplied question."
                                                        ),
                                                    },
                                                },
                                            },
                                            "agent_results": {
                                                "type": "object",
                                                "description": (
                                                    "Per-agent execution status, useful for "
                                                    "confirming all four agents ran."
                                                ),
                                                "properties": {
                                                    "clause_agent": {
                                                        "type": "string",
                                                        "description": "Clause agent status.",
                                                    },
                                                    "risk_agent": {
                                                        "type": "string",
                                                        "description": "Risk agent status.",
                                                    },
                                                    "compliance_agent": {
                                                        "type": "string",
                                                        "description": "Compliance agent status.",
                                                    },
                                                    "response_agent": {
                                                        "type": "string",
                                                        "description": "Response agent status.",
                                                    },
                                                },
                                            },
                                            "agent_runtime": {
                                                "type": "string",
                                                "description": (
                                                    "ARN of the response agent runtime."
                                                ),
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "Invalid input, typically a missing contract_text"},
                        "401": {"description": "Unauthorized, invalid or missing OAuth token"},
                        "403": {"description": "Forbidden, insufficient OAuth scopes"},
                        "500": {"description": "Internal server error invoking the agents"},
                    },
                }
            }
        },
    }


def main():
    print("🚀 Quick Suite Integration Deployment")
    print("=" * 50)

    account_id, region = get_aws_account_info()
    if not account_id:
        return 1
    print(f"📋 Account: {account_id}")
    print(f"🌍 Region:  {region}")

    print("\n🔍 Loading agent runtimes from AgentCore deployed state...")
    response_agent_arn, all_agent_arns = load_agent_arns()
    if not response_agent_arn:
        return 1

    iam_client = boto3.client("iam", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)
    cognito_client = boto3.client("cognito-idp", region_name=region)
    apigw_client = boto3.client("apigateway", region_name=region)

    print("\n🔧 Creating Lambda execution role...")
    role_arn = create_lambda_role(iam_client)
    if not role_arn:
        return 1

    print("\n🔧 Deploying Lambda function...")
    function_arn = deploy_lambda(lambda_client, role_arn, response_agent_arn, all_agent_arns)
    if not function_arn:
        return 1

    print("\n🔧 Creating Cognito user pool...")
    cognito_config = create_cognito_user_pool(cognito_client, region)
    if not cognito_config:
        return 1

    print("\n🔧 Creating API Gateway...")
    api_id, base_url = create_api_gateway(
        apigw_client, lambda_client, function_arn, cognito_config, account_id, region
    )
    if not api_id:
        return 1

    print("\n📄 Generating OpenAPI schema...")
    openapi_schema = generate_openapi_schema(base_url, cognito_config)

    config = {
        "response_agent_arn": response_agent_arn,
        "all_agent_arns": all_agent_arns,
        "lambda_function_arn": function_arn,
        "api_gateway_id": api_id,
        "api_url": base_url,
        "cognito": cognito_config,
        "quicksuite_integration": {
            "client_id": cognito_config["client_id"],
            "client_secret": cognito_config["client_secret"],
            "token_url": cognito_config["token_url"],
            "scopes": cognito_config["scopes"],
            "api_endpoint": base_url,
        },
    }

    with open("quicksuite_integration_config.json", "w") as f:
        json.dump(config, f, indent=2)
    with open("quicksuite_openapi_schema.json", "w") as f:
        json.dump(openapi_schema, f, indent=2)

    print("\n" + "=" * 50)
    print("🎉 QUICK SUITE INTEGRATION READY")
    print("=" * 50)
    print("\n📊 Configuration:")
    print(f"   • API endpoint: {base_url}")
    print(f"   • Client ID:    {cognito_config['client_id']}")
    print(f"   • Token URL:    {cognito_config['token_url']}")
    print(f"   • Scope:        {cognito_config['scopes'][0]}")
    print("   • Client secret: written to quicksuite_integration_config.json")

    print("\n📄 Files created (both are gitignored):")
    print("   • quicksuite_integration_config.json")
    print("   • quicksuite_openapi_schema.json")

    print("\n🔧 Quick Suite setup:")
    print("   1. Connectors → Create for your team → OpenAPI Specification")
    print("   2. Import quicksuite_openapi_schema.json")
    print("   3. Choose Service authentication and supply the client ID, secret, token URL")

    print("\n⚠️  API Gateway REST APIs cap integrations at 29 seconds. Analysis runs")
    print("    ~20s warm, so send one throwaway request to warm the agents first.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
