import os
from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """
    Get Bedrock model client using AWS IAM credentials.
    Defaults to Claude 3 Haiku for high-speed, low-cost execution ($0.00025 / 1k tokens).
    """
    model_id = os.getenv(
        "BEDROCK_MODEL_ID",
        "anthropic.claude-3-haiku-20240307-v1:0"
    )
    region = os.getenv("AWS_REGION", "us-east-1")
    return BedrockModel(model_id=model_id, region_name=region)
