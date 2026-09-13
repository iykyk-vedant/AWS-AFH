"""
Language detector utility.

Detects whether a service/file uses Python or Node.js.
"""


def detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    if file_path.endswith(".py"):
        return "python"
    elif file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
        return "node"
    elif file_path.endswith(".json") and "package" in file_path:
        return "node"
    elif file_path.endswith(".txt") and "requirements" in file_path:
        return "python"
    return "unknown"


def detect_service_language(service_name: str) -> str:
    """Detect language from service directory name."""
    service_lower = service_name.lower()
    if "python" in service_lower or "flask" in service_lower:
        return "python"
    elif "node" in service_lower or "express" in service_lower:
        return "node"
    return "unknown"


def get_test_command(language: str) -> str:
    """Get the test command for a language."""
    if language == "python":
        return "python -m pytest tests/ -v --tb=short"
    elif language == "node":
        return "npm test"
    return ""


def get_service_dir(service_name: str) -> str:
    """Map service name to directory in the target repository."""
    service_map = {
        "python-service": "python-service",
        "node-service": "node-service",
        "checkout-service": "python-service",
        "payment-service": "python-service",
        "auth-service": "python-service",
        "product-service": "python-service",
        "order-service": "python-service",
        "user-service": "node-service",
        "report-service": "node-service",
    }
    return service_map.get(service_name.lower(), service_name)
