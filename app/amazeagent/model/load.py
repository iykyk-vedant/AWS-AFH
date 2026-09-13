import os
from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """
    Get Bedrock model client using AWS IAM credentials.
    Defaults to Claude Haiku 4.5 for high-speed, low-cost execution.
    """
    model_id = os.getenv(
        "BEDROCK_MODEL_ID",
        "anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    region = os.getenv("AWS_REGION", "us-east-1")
    return BedrockModel(model_id=model_id, region_name=region)

