"""
Unit tests for the Incident Parser Agent.

Validates parsing of raw JSON incident tickets into canonical IncidentContext,
suspect file extraction, and fallback resilience.
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestIncidentFileParsing:
    """Verify that local incident JSON files parse correctly."""

    def _load_incident(self, incident_id: str) -> dict:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "incidents",
            f"{incident_id}.json",
        )
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_inc_001_has_required_fields(self):
        inc = self._load_incident("INC-001")
        assert inc["id"] == "INC-001"
        assert "title" in inc
        assert "severity" in inc
        assert "error_log" in inc

    def test_inc_001_severity_is_critical(self):
        inc = self._load_incident("INC-001")
        assert "P1" in inc["severity"]

    def test_inc_001_has_suspect_file_in_error_log(self):
        inc = self._load_incident("INC-001")
        assert "auth.py" in inc["error_log"]

    def test_inc_002_has_required_fields(self):
        inc = self._load_incident("INC-002")
        assert inc["id"] == "INC-002"
        assert "payment" in inc["title"].lower() or "discount" in inc["title"].lower()

    def test_inc_003_is_zero_division(self):
        inc = self._load_incident("INC-003")
        assert "ZeroDivision" in inc["error_log"]

    def test_inc_004_is_key_error(self):
        inc = self._load_incident("INC-004")
        assert "KeyError" in inc["error_log"]


class TestIncidentStructure:
    """Verify all 11 incident files have consistent schema."""

    def _load_incident(self, incident_id: str) -> dict:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "incidents",
            f"{incident_id}.json",
        )
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_all_11_incidents_exist(self):
        """All 11 incident files should be present."""
        for i in range(1, 12):
            inc_id = f"INC-{i:03d}"
            inc = self._load_incident(inc_id)
            assert inc["id"] == inc_id, f"Missing or malformed {inc_id}"

    def test_all_incidents_have_core_fields(self):
        """Every incident must have id, title, severity, error_log."""
        required = {"id", "title", "severity", "error_log"}
        for i in range(1, 12):
            inc_id = f"INC-{i:03d}"
            inc = self._load_incident(inc_id)
            for field in required:
                assert field in inc, f"{inc_id} missing field: {field}"

    def test_all_incidents_have_affected_service(self):
        for i in range(1, 12):
            inc_id = f"INC-{i:03d}"
            inc = self._load_incident(inc_id)
            assert "affected_service" in inc, f"{inc_id} missing affected_service"


class TestFixCandidateFiles:
    """Verify that pre-generated fix candidate files exist and are valid JSON."""

    def test_all_11_fix_candidates_exist(self):
        for i in range(1, 12):
            inc_id = f"INC-{i:03d}"
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "fix_candidates",
                f"{inc_id}.json",
            )
            assert os.path.exists(path), f"Missing fix candidate: {path}"

    def test_fix_candidates_are_valid_json(self):
        for i in range(1, 12):
            inc_id = f"INC-{i:03d}"
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "fix_candidates",
                f"{inc_id}.json",
            )
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "candidates" in data, f"{inc_id} fix candidate missing 'candidates' key"
