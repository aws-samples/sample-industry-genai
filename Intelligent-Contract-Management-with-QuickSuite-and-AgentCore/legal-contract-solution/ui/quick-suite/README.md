# Amazon Quick Suite Integration

## Why Amazon Cognito User Pools Are the Preferred Solution

Amazon Cognito User Pools provide optimal authentication for Quick Suite integration through OAuth 2.0 client credentials flow for service-to-service communication. Benefits include:

- **AWS Ecosystem Integration**: Unified security posture with IAM integration
- **Enterprise Security**: Scoped access control, token lifecycle management, audit logging
- **Cost-Effective**: Pay-per-use model with generous free tiers
- **Standard OAuth 2.0**: Compatible with Quick Suite's authentication requirements

## Deployment

Deploy the Quick Suite integration using the main deployment script:

```bash
python deployment/deploy_quicksuite_integration.py
```

This creates:
- Lambda function (`agentcore_invoke_lambda.py`)
- API Gateway endpoint with OpenAPI schema
- Cognito User Pool for OAuth authentication
- All necessary IAM roles and policies

## Configuration

After deployment, use the generated credentials from `deployment/outputs/quicksuite_integration_config.json`:
- Import the OpenAPI schema into Quick Suite
- Configure OAuth 2.0 client credentials flow
- Use the provided Client ID, Secret, and Token URL

For detailed deployment instructions, see the main project README.