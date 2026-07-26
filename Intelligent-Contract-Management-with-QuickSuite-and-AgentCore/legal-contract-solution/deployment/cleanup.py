#!/usr/bin/env python3
"""Tear down the deployed legal contract analyzer.

Removes, in dependency order:

  1. Quick Suite integration layer  - API Gateway, Lambda, Cognito user pool, IAM role
                                      (read from quicksuite_integration_config.json)
  2. AgentCore agents               - by deleting the CDK CloudFormation stack
                                      (read from agentcore/.cli/deployed-state.json)
  3. Generated local files

The AgentCore CLI has no `destroy` command; `agentcore remove` only edits local
project config. Deleting the CloudFormation stack that `agentcore deploy` created
is the supported teardown path.

Run from the legal-contract-solution directory:

    python deployment/cleanup.py           # prompts for confirmation
    python deployment/cleanup.py --yes     # non-interactive

NOT removed (may be shared with other projects, delete manually if desired):
  • the CDK bootstrap stack (CDKToolkit) and its staging bucket
  • ECR images built for the agents
  • CloudWatch log groups
"""

import argparse
import json
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

QS_CONFIG_PATH = "quicksuite_integration_config.json"
DEPLOYED_STATE_PATH = "agentcore/.cli/deployed-state.json"
LAMBDA_ROLE_NAME = "QuickSuiteLegalContractLambdaRole"

GENERATED_FILES = (
    QS_CONFIG_PATH,
    "quicksuite_openapi_schema.json",
    "quicksuite_lambda.zip",
    ".bedrock_agentcore.yaml",  # legacy artifact from the old starter toolkit
)


def get_region():
    return boto3.Session().region_name or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"⚠️  {path} is not valid JSON: {e}")
        return None


def get_stack_name(state, target="default"):
    if not state:
        return None
    return state.get("targets", {}).get(target, {}).get("resources", {}).get("stackName")


def confirm(stack_name, qs_config, assume_yes):
    print("🧹 Teardown plan")
    print("=" * 60)

    if qs_config:
        print("Quick Suite integration layer:")
        print(f"  • API Gateway:   {qs_config.get('api_gateway_id', '(unknown)')}")
        print(f"  • Lambda:        {qs_config.get('lambda_function_arn', '(unknown)')}")
        cog = qs_config.get("cognito", {})
        print(f"  • Cognito pool:  {cog.get('user_pool_id', '(unknown)')}")
        print(f"  • IAM role:      {LAMBDA_ROLE_NAME}")
    else:
        print(f"Quick Suite integration layer: SKIPPED ({QS_CONFIG_PATH} not found)")

    if stack_name:
        print("\nAgentCore agents:")
        print(f"  • CloudFormation stack: {stack_name}")
        print("    (deletes all four agent runtimes and their execution roles)")
    else:
        print(f"\nAgentCore agents: SKIPPED ({DEPLOYED_STATE_PATH} not found)")

    print("\nLocal files:", ", ".join(GENERATED_FILES))
    print("=" * 60)

    if not stack_name and not qs_config:
        print("Nothing to do.")
        return False

    if assume_yes:
        return True

    print("\n⚠️  This permanently deletes the resources listed above.")
    answer = input("Type 'delete' to proceed: ").strip()
    if answer != "delete":
        print("Aborted. Nothing was deleted.")
        return False
    return True


def cleanup_api_gateway(qs_config, region):
    api_id = (qs_config or {}).get("api_gateway_id")
    if not api_id:
        print("⚠️  No API Gateway id in config, skipping")
        return
    try:
        boto3.client("apigateway", region_name=region).delete_rest_api(restApiId=api_id)
        print(f"✅ Deleted API Gateway: {api_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            print(f"ℹ️  API Gateway {api_id} already gone")
        else:
            print(f"❌ Failed to delete API Gateway: {e}")


def cleanup_lambda(qs_config, region):
    arn = (qs_config or {}).get("lambda_function_arn")
    if not arn:
        print("⚠️  No Lambda ARN in config, skipping")
        return
    function_name = arn.split(":")[-1]
    client = boto3.client("lambda", region_name=region)

    # A function URL, if one was created, must go before the function itself.
    try:
        client.delete_function_url_config(FunctionName=function_name)
        print(f"✅ Deleted function URL for {function_name}")
    except ClientError:
        pass

    try:
        client.delete_function(FunctionName=function_name)
        print(f"✅ Deleted Lambda function: {function_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"ℹ️  Lambda {function_name} already gone")
        else:
            print(f"❌ Failed to delete Lambda: {e}")


def cleanup_cognito(qs_config, region):
    cognito_config = (qs_config or {}).get("cognito")
    if not cognito_config:
        print("⚠️  No Cognito config, skipping")
        return

    client = boto3.client("cognito-idp", region_name=region)
    user_pool_id = cognito_config.get("user_pool_id")

    # The custom domain must be deleted before the pool.
    domain_url = cognito_config.get("domain_url", "")
    if domain_url:
        domain = domain_url.split("//")[-1].split(".")[0]
        try:
            client.delete_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
            print(f"✅ Deleted Cognito domain: {domain}")
            print("⏳ Waiting 15s for domain deletion to settle...")
            time.sleep(15)
        except ClientError as e:
            print(f"⚠️  Domain deletion: {e}")

    try:
        client.delete_user_pool(UserPoolId=user_pool_id)
        print(f"✅ Deleted Cognito user pool: {user_pool_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"ℹ️  User pool {user_pool_id} already gone")
        else:
            print(f"❌ Failed to delete user pool: {e}")


def cleanup_iam_role(region):
    client = boto3.client("iam", region_name=region)

    try:
        client.get_role(RoleName=LAMBDA_ROLE_NAME)
    except ClientError:
        print(f"ℹ️  IAM role {LAMBDA_ROLE_NAME} already gone")
        return

    # A role cannot be deleted while policies remain attached.
    try:
        for p in client.list_attached_role_policies(RoleName=LAMBDA_ROLE_NAME)["AttachedPolicies"]:
            client.detach_role_policy(RoleName=LAMBDA_ROLE_NAME, PolicyArn=p["PolicyArn"])
            print(f"   detached {p['PolicyName']}")
        for name in client.list_role_policies(RoleName=LAMBDA_ROLE_NAME)["PolicyNames"]:
            client.delete_role_policy(RoleName=LAMBDA_ROLE_NAME, PolicyName=name)
            print(f"   removed inline policy {name}")
        client.delete_role(RoleName=LAMBDA_ROLE_NAME)
        print(f"✅ Deleted IAM role: {LAMBDA_ROLE_NAME}")
    except ClientError as e:
        print(f"❌ Failed to delete IAM role: {e}")


def cleanup_agent_stack(stack_name, region, wait=True):
    """Delete the CDK stack that agentcore deploy created."""
    if not stack_name:
        print("⚠️  No stack name in deployed state, skipping agent teardown")
        return

    cfn = boto3.client("cloudformation", region_name=region)
    try:
        cfn.describe_stacks(StackName=stack_name)
    except ClientError:
        print(f"ℹ️  Stack {stack_name} not found, already deleted")
        return

    try:
        cfn.delete_stack(StackName=stack_name)
        print(f"🗑️  Deleting stack {stack_name} (this removes all agent runtimes)")
    except ClientError as e:
        print(f"❌ Failed to initiate stack deletion: {e}")
        return

    if not wait:
        print("   Not waiting for completion. Check the CloudFormation console.")
        return

    print("⏳ Waiting for stack deletion (up to ~15 minutes)...")
    try:
        cfn.get_waiter("stack_delete_complete").wait(
            StackName=stack_name,
            WaiterConfig={"Delay": 15, "MaxAttempts": 60},
        )
        print(f"✅ Deleted stack: {stack_name}")
    except Exception as e:
        print(f"⚠️  Stack deletion did not confirm within the wait window: {e}")
        print("   Check the CloudFormation console for the final state.")


def cleanup_local_files():
    for path in GENERATED_FILES:
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"✅ Removed {path}")
            except OSError as e:
                print(f"❌ Failed to remove {path}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Tear down the legal contract analyzer.")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--target", default="default", help="AgentCore deployment target")
    parser.add_argument("--no-wait", action="store_true",
                        help="do not wait for stack deletion to finish")
    parser.add_argument("--keep-agents", action="store_true",
                        help="tear down only the Quick Suite layer, leave agents running")
    args = parser.parse_args()

    if not os.path.exists("deployment"):
        print("❌ Run this from the legal-contract-solution directory.")
        return 1

    region = get_region()
    qs_config = load_json(QS_CONFIG_PATH)
    state = load_json(DEPLOYED_STATE_PATH)
    stack_name = None if args.keep_agents else get_stack_name(state, args.target)

    if not confirm(stack_name, qs_config, args.yes):
        return 0

    print(f"\n🌍 Region: {region}")

    if qs_config:
        print("\n🗑️  Step 1: API Gateway")
        cleanup_api_gateway(qs_config, region)

        print("\n🗑️  Step 2: Lambda function")
        cleanup_lambda(qs_config, region)

        print("\n🗑️  Step 3: Cognito user pool")
        cleanup_cognito(qs_config, region)

        print("\n🗑️  Step 4: IAM role")
        cleanup_iam_role(region)

    if stack_name:
        print("\n🗑️  Step 5: AgentCore agents (CloudFormation stack)")
        cleanup_agent_stack(stack_name, region, wait=not args.no_wait)

    print("\n🗑️  Step 6: Generated local files")
    cleanup_local_files()

    print("\n" + "=" * 60)
    print("🎉 Teardown complete")
    print("\nNot removed (may be shared, delete manually if you want them gone):")
    print("  • CDK bootstrap stack (CDKToolkit) and its staging bucket")
    print("  • ECR images built for the agents")
    print("  • CloudWatch log groups for the Lambda and agent runtimes")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
