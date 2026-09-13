"""
Unit tests for the Language Detector utility.

Validates file extension to language mapping, test command generation,
and service directory mapping.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.language_detector import detect_language, get_test_command, get_service_dir


class TestDetectLanguage:
    """Verify file extension to language detection."""

    def test_python_file(self):
        assert detect_language("app/routes/auth.py") == "python"

    def test_javascript_file(self):
        assert detect_language("src/index.js") == "node"

    def test_typescript_file(self):
        assert detect_language("src/app.ts") == "node"

    def test_unknown_extension(self):
        result = detect_language("README.md")
        assert result in ("unknown", "")


class TestGetTestCommand:
    """Verify test command generation per language."""

    def test_python_test_command(self):
        cmd = get_test_command("python")
        assert "pytest" in cmd

    def test_node_test_command(self):
        cmd = get_test_command("node")
        assert "npm test" in cmd

    def test_unknown_returns_empty(self):
        cmd = get_test_command("rust")
        assert cmd == ""


class TestGetServiceDir:
    """Verify service name to directory mapping."""

    def test_python_service(self):
        assert get_service_dir("python-service") == "python-service"

    def test_node_service(self):
        assert get_service_dir("node-service") == "node-service"

    def test_checkout_maps_to_python(self):
        assert get_service_dir("checkout-service") == "python-service"

    def test_auth_maps_to_python(self):
        assert get_service_dir("auth-service") == "python-service"

    def test_unknown_service_returns_itself(self):
        assert get_service_dir("custom-thing") == "custom-thing"
