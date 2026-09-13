"""
Verification script to test Bedrock Model Access and Strands integration.
"""
import sys
import os
import boto3

MODELS_TO_TEST = [
    ("Claude Sonnet 4.6 (Strands default)", "global.anthropic.claude-sonnet-4-6"),
    ("Claude Sonnet 4.6 (Direct ID)", "anthropic.claude-sonnet-4-6"),
    ("Claude 3.5 Sonnet (US Profile)", "us.anthropic.claude-3-5-sonnet-20241022-v2:0"),
    ("Claude 3.5 Haiku", "us.anthropic.claude-3-5-haiku-20241022-v1:0"),
    ("Amazon Nova Pro", "amazon.nova-pro-v1:0"),
    ("Amazon Nova Lite", "amazon.nova-lite-v1:0"),
]

def test_bedrock():
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    print("=" * 60)
    print(f"Testing AWS Bedrock Model Access in {region}...")
    print("=" * 60)
    
    client = boto3.client("bedrock-runtime", region_name=region)
    available_models = []

    for name, model_id in MODELS_TO_TEST:
        try:
            resp = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "Hello, reply with 'READY'."}]}],
                inferenceConfig={"maxTokens": 10, "temperature": 0.1}
            )
            output_text = resp["output"]["message"]["content"][0]["text"].strip()
            print(f"  [OK] {name} ({model_id}) -> {output_text}")
            available_models.append((name, model_id))
        except Exception as e:
            err_msg = str(e)
            if "AccessDeniedException" in err_msg or "ValidationException" in err_msg or "Operation not allowed" in err_msg:
                print(f"  [--] {name} ({model_id}) -> Access not enabled yet")
            else:
                print(f"  [ERR] {name} ({model_id}) -> {err_msg[:80]}")

    print("=" * 60)
    if available_models:
        print(f"SUCCESS: {len(available_models)} model(s) ready to use!")
        print("Recommended model for Strands:")
        print(f"  -> {available_models[0][1]}")
    else:
        print("NO MODELS ACCESSIBLE YET.")
        print("Please complete the 'Model access' steps in the AWS Console.")
    print("=" * 60)

if __name__ == "__main__":
    test_bedrock()
