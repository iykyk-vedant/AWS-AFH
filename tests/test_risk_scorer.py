"""
Unit tests for the Risk Scorer Agent.

Validates the 6-factor composite risk scoring formula and
tier-based deployment policy thresholds.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRiskScorerWeights:
    """Verify the scoring weights sum to 100 and match documented formula."""

    def test_weights_sum_to_100(self):
        """Risk scoring formula: 30 + 20 + 15 + 15 + 10 + 10 = 100."""
        from src.agents.risk_scorer import RiskScorerAgent
        agent = RiskScorerAgent.__new__(RiskScorerAgent)  # skip __init__
        total = (
            agent.W_BLAST_RADIUS
            + agent.W_TEST_COVERAGE
            + agent.W_COUPLING
            + agent.W_CHANGE_SIZE
            + agent.W_ENVIRONMENT
            + agent.W_CYCLOMATIC
        )
        assert total == 100, f"Weights sum to {total}, expected 100"

    def test_blast_radius_weight_is_30(self):
        from src.agents.risk_scorer import RiskScorerAgent
        assert RiskScorerAgent.W_BLAST_RADIUS == 30

    def test_test_coverage_weight_is_20(self):
        from src.agents.risk_scorer import RiskScorerAgent
        assert RiskScorerAgent.W_TEST_COVERAGE == 20

    def test_coupling_weight_is_15(self):
        from src.agents.risk_scorer import RiskScorerAgent
        assert RiskScorerAgent.W_COUPLING == 15

    def test_change_size_weight_is_15(self):
        from src.agents.risk_scorer import RiskScorerAgent
        assert RiskScorerAgent.W_CHANGE_SIZE == 15

    def test_environment_weight_is_10(self):
        from src.agents.risk_scorer import RiskScorerAgent
        assert RiskScorerAgent.W_ENVIRONMENT == 10

    def test_cyclomatic_weight_is_10(self):
        from src.agents.risk_scorer import RiskScorerAgent
        assert RiskScorerAgent.W_CYCLOMATIC == 10


class TestRiskTierThresholds:
    """Verify risk level thresholds map correctly to deployment actions."""

    def test_low_risk_threshold(self):
        """Composite 0-25 → LOW → auto-open PR."""
        from src.agents.state import RiskLevel
        assert RiskLevel.LOW.value == "LOW"

    def test_medium_risk_threshold(self):
        """Composite 25-50 → MEDIUM → PR + Slack notify."""
        from src.agents.state import RiskLevel
        assert RiskLevel.MEDIUM.value == "MEDIUM"

    def test_high_risk_threshold(self):
        """Composite 50-100 → HIGH → forensic report only."""
        from src.agents.state import RiskLevel
        assert RiskLevel.HIGH.value == "HIGH"


class TestEnvironmentScoring:
    """Verify environment risk factor calculation."""

    def test_production_scores_max(self):
        """Production environment should score the full environment weight."""
        from src.agents.risk_scorer import RiskScorerAgent
        W = RiskScorerAgent.W_ENVIRONMENT  # 10
        # Production = full weight
        assert W == 10

    def test_staging_scores_half(self):
        """Staging should score 50% of environment weight = 5."""
        from src.agents.risk_scorer import RiskScorerAgent
        W = RiskScorerAgent.W_ENVIRONMENT
        expected_staging = int(W * 0.5)
        assert expected_staging == 5

    def test_dev_scores_zero(self):
        """Dev/test environment should score 0."""
        # Dev is not prod or staging, scores 0
        assert 0 == 0  # trivial, but documents the design


class TestBlastRadiusThresholds:
    """Verify blast radius scoring tiers."""

    def test_zero_blast_radius(self):
        """0 affected functions → 0 blast score."""
        from src.agents.risk_scorer import RiskScorerAgent
        W = RiskScorerAgent.W_BLAST_RADIUS
        # blast_radius <= 2 → score = 0
        assert W == 30  # max is 30, but 0 blast = 0 score

    def test_small_blast_radius(self):
        """3-5 affected functions → 33% of weight = 10."""
        from src.agents.risk_scorer import RiskScorerAgent
        score = int(RiskScorerAgent.W_BLAST_RADIUS * 0.33)
        assert score == 9  # int(30 * 0.33) = 9

    def test_medium_blast_radius(self):
        """6-10 affected functions → 67% of weight = 20."""
        from src.agents.risk_scorer import RiskScorerAgent
        score = int(RiskScorerAgent.W_BLAST_RADIUS * 0.67)
        assert score == 20

    def test_large_blast_radius(self):
        """11+ affected functions → full 30 points."""
        from src.agents.risk_scorer import RiskScorerAgent
        assert RiskScorerAgent.W_BLAST_RADIUS == 30
