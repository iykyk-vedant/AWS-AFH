import os
from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """Get Bedrock model client using AWS IAM credentials."""
    model_id = os.getenv(
        "BEDROCK_MODEL_ID",
        "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    region = os.getenv("AWS_REGION", "us-east-1")
    return BedrockModel(model_id=model_id, region_name=region)
