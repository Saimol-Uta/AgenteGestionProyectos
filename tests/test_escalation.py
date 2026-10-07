"""Tests for isolated EscalationGate."""

import unittest

from agente_gestion.domain.findings import CandidateFinding, FindingCategory
from agente_gestion.domain.policies import QualityRule
from agente_gestion.application.escalation import (
    EscalationGate,
    EscalationPolicy,
    EscalationDecision,
)


class EscalationGateTestCase(unittest.TestCase):
    def setUp(self):
        self.gate = EscalationGate()

    def test_severe_category_escalates_with_reason(self):
        finding = CandidateFinding(
            id="f-sql",
            category=FindingCategory.inyeccion_sql.value,
            file_path="src/repo.py",
            line=10,
            commit_sha="abc1234",
            evidence="cursor.execute(f'SELECT * FROM users WHERE id={uid}')",
            suggested_fix="use parameterized queries",
            evidence_ids=["ev-1"],
            confidence=0.95,
        )
        decision = self.gate.evaluate_finding(finding)
        self.assertTrue(decision.should_escalate)
        self.assertTrue(any("severe" in r.lower() or "inyeccion_sql" in r.lower() for r in decision.reasons))

    def test_low_confidence_escalates_with_reason(self):
        finding = CandidateFinding(
            id="f-low-conf",
            category=FindingCategory.otros.value,
            file_path="src/repo.py",
            line=12,
            commit_sha="abc1234",
            evidence="foo()",
            suggested_fix="check foo",
            evidence_ids=["ev-1"],
            confidence=0.4,  # Below default threshold 0.7
        )
        decision = self.gate.evaluate_finding(finding)
        self.assertTrue(decision.should_escalate)
        self.assertTrue(any("confidence" in r.lower() for r in decision.reasons))

    def test_contradiction_signal_escalates_with_reason(self):
        finding = CandidateFinding(
            id="f-contra",
            category=FindingCategory.division_cero.value,
            file_path="src/calc.py",
            line=5,
            commit_sha="abc1234",
            evidence="x / y",
            suggested_fix="check y != 0",
            evidence_ids=["ev-1"],
            confidence=0.9,
        )
        decision = self.gate.evaluate_finding(finding, has_contradictions=True)
        self.assertTrue(decision.should_escalate)
        self.assertTrue(any("contradiction" in r.lower() for r in decision.reasons))

    def test_insufficient_evidence_escalates_with_reason(self):
        finding = CandidateFinding(
            id="f-no-ev",
            category=FindingCategory.division_cero.value,
            file_path="src/calc.py",
            line=5,
            commit_sha="abc1234",
            evidence="x / y",
            suggested_fix="check y != 0",
            evidence_ids=[],  # No evidence IDs attached!
            confidence=0.85,
        )
        decision = self.gate.evaluate_finding(finding)
        self.assertTrue(decision.should_escalate)
        self.assertTrue(any("insufficient" in r.lower() or "evidence" in r.lower() for r in decision.reasons))

    def test_mandatory_rule_association_escalates_with_reason(self):
        finding = CandidateFinding(
            id="f-mand",
            category=FindingCategory.division_cero.value,
            file_path="src/calc.py",
            line=5,
            commit_sha="abc1234",
            evidence="x / y",
            suggested_fix="check y != 0",
            evidence_ids=["ev-1"],
            confidence=0.85,
            rule_id="RULE_MANDATORY_1",
        )
        mandatory_rule = QualityRule(
            id="RULE_MANDATORY_1",
            description="No zero divisions",
            metric_key="div_zero",
            operator="==",
            threshold=0,
            mandatory=True,
        )
        decision = self.gate.evaluate_finding(finding, rule=mandatory_rule)
        self.assertTrue(decision.should_escalate)
        self.assertTrue(any("mandatory" in r.lower() for r in decision.reasons))

    def test_routine_finding_does_not_escalate_automatically(self):
        # High confidence, non-severe category, evidence present, non-mandatory rule, no contradictions
        finding = CandidateFinding(
            id="f-routine",
            category=FindingCategory.division_cero.value,
            file_path="src/calc.py",
            line=5,
            commit_sha="abc1234",
            evidence="x / y",
            suggested_fix="check y != 0",
            evidence_ids=["ev-1"],
            confidence=0.95,
            rule_id="RULE_OPTIONAL",
        )
        optional_rule = QualityRule(
            id="RULE_OPTIONAL",
            description="Optional check",
            metric_key="div_zero",
            operator="==",
            threshold=0,
            mandatory=False,
        )
        decision = self.gate.evaluate_finding(finding, rule=optional_rule)
        # MUST NOT escalate every finding automatically!
        self.assertFalse(decision.should_escalate)
        self.assertEqual(decision.reasons, [])

    def test_seguridad_and_casing_spacing_severity_semantics(self):
        # Category casing or spacing must not prevent severe category escalation
        test_cases = [
            "Seguridad",
            "  Seguridad  ",
            "SEGURIDAD",
            "seguridad",
            "Critical",
            " CRITICAL ",
            "High",
            " HIGH ",
            " Inyeccion_SQL ",
        ]
        for cat in test_cases:
            finding = CandidateFinding(
                id=f"f-{cat.strip()}",
                category=cat,
                file_path="src/auth.py",
                line=20,
                commit_sha="c0ffee1",
                evidence="hardcoded_secret = '1234'",
                suggested_fix="use env var",
                evidence_ids=["c0ffee1"],
                confidence=0.95,  # High confidence
            )
            decision = self.gate.evaluate_finding(finding)
            self.assertTrue(
                decision.should_escalate,
                f"Expected finding with category '{cat}' to escalate as severe category",
            )
            self.assertTrue(
                any("severe" in r.lower() for r in decision.reasons),
                f"Expected severe category reason for category '{cat}'",
            )


if __name__ == "__main__":
    unittest.main()
