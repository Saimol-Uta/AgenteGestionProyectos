"""Comprehensive Quality Benchmark & False Positive/Negative Verification (REQ-07, REQ-10, REQ-11).

Evaluates:
1. CI artifact and quality criteria verification beyond localized diffs.
2. Ground truth benchmark measuring:
   - True Positives (TP): Legitimate code defects accurately accepted by semantic validation.
   - False Positive Traps (FP Traps): LLM hallucinations (absence claims in partial snippets,
     inferred missing tests from unavailable CI, out-of-bounds lines) that MUST be rejected.
   - True Negatives (TN): Clean code or successfully rejected traps.
   - False Negatives (FN): Undetected actual defects.
3. Confusion matrix metrics: Precision, Recall, Specificity, False Positive Rate (FPR), False Negative Rate (FNR).
"""

from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.findings import CandidateFinding, FindingSeverity
from agente_gestion.domain.policies import QualityRule, RuleEvaluationStatus, evaluate_single_rule
from agente_gestion.application.validation import SemanticValidator


class BenchmarkSample(BaseModel):
    """Ground truth labeled sample for evaluation."""
    model_config = ConfigDict(frozen=True)

    sample_id: str
    name: str
    category: Literal[
        "visible_vulnerability",
        "partial_absence_trap",
        "ci_coverage_trap",
        "line_out_of_bounds_trap",
        "clean_code",
    ]
    ground_truth_is_defect: bool
    description: str
    snippets: list[CodeDiffSnippet]
    rule: QualityRule
    candidate_finding: Optional[CandidateFinding] = None
    ci_result: Optional[CIRunResult] = None


class ConfusionMatrixMetrics(BaseModel):
    """Calculated metrics from ground truth benchmark."""
    samples_total: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float = Field(description="TP / (TP + FP)")
    recall: float = Field(description="TP / (TP + FN)")
    specificity: float = Field(description="TN / (TN + FP)")
    false_positive_rate: float = Field(description="FP / (FP + TN)")
    false_negative_rate: float = Field(description="FN / (TP + FN)")


class QualityBenchmarkReport(BaseModel):
    """Full benchmark audit report."""
    metrics: ConfusionMatrixMetrics
    sample_outcomes: list[dict[str, Any]]
    ci_rules_evaluated: list[dict[str, Any]]
    passed_quality_gate: bool
    summary: str


def get_standard_ground_truth_dataset() -> list[BenchmarkSample]:
    """Returns curated ground-truth test suite for code quality evaluation."""
    rule_security = QualityRule(
        id="Q_SEC_01",
        description="Zero critical security vulnerabilities (SQL injection, leaked keys)",
        metric_key="open_critical_findings",
        source="review",
        operator="==",
        threshold=0,
        mandatory=True,
    )
    rule_coverage = QualityRule(
        id="Q_CI_01",
        description="Minimum unit test coverage 80%",
        metric_key="coverage_percentage",
        source="ci",
        operator=">=",
        threshold=80.0,
        mandatory=True,
    )

    # 1. True Positive: Visible SQL Injection
    snip_sql = CodeDiffSnippet(
        repository="HORMIGESTION",
        commit_sha="a1b2c3d4",
        file_path="src/db/orders.py",
        lines=[42],
        content='cursor.execute(f"SELECT * FROM orders WHERE id = \'{user_input}\'")',
        evidence_scope="diff",
        is_complete=False,
    )
    finding_sql = CandidateFinding(
        id="CQ-001",
        rule_id="Q_SEC_01",
        claim="Direct string interpolation into SQL query allows SQL injection vulnerability.",
        severity=FindingSeverity.critical,
        evidence_ids=["a1b2c3d4"],
        commit_sha="a1b2c3d4",
        file_path="src/db/orders.py",
        line=42,
        confidence=0.98,
        claim_basis="visible_code",
    )
    s1 = BenchmarkSample(
        sample_id="SAMPLE-01-SQLI",
        name="Visible SQL Injection",
        category="visible_vulnerability",
        ground_truth_is_defect=True,
        description="Real visible SQL string formatting flaw in active code.",
        snippets=[snip_sql],
        rule=rule_security,
        candidate_finding=finding_sql,
    )

    # 2. True Positive: Hardcoded AWS Credential
    snip_secret = CodeDiffSnippet(
        repository="HORMIGESTION",
        commit_sha="b2c3d4e5",
        file_path="config/aws.py",
        lines=[15],
        content='AWS_SECRET_KEY = "AKIA1234567890ABCDEF1234567890"',
        evidence_scope="diff",
        is_complete=False,
    )
    finding_secret = CandidateFinding(
        id="CQ-002",
        rule_id="Q_SEC_01",
        claim="Hardcoded AWS secret key committed to source code.",
        severity=FindingSeverity.critical,
        evidence_ids=["b2c3d4e5"],
        commit_sha="b2c3d4e5",
        file_path="config/aws.py",
        line=15,
        confidence=0.99,
        claim_basis="visible_code",
    )
    s2 = BenchmarkSample(
        sample_id="SAMPLE-02-LEAK",
        name="Hardcoded Secret Key",
        category="visible_vulnerability",
        ground_truth_is_defect=True,
        description="Real visible hardcoded key in configuration snippet.",
        snippets=[snip_secret],
        rule=rule_security,
        candidate_finding=finding_secret,
    )

    # 3. False Positive Trap: Absence Claim on Partial Diff Snippet
    snip_partial = CodeDiffSnippet(
        repository="HORMIGESTION",
        commit_sha="c3d4e5f6",
        file_path="src/services/billing.py",
        lines=[88],
        content="def process_payment(amount: float) -> bool:",
        evidence_scope="diff",
        is_complete=False,
    )
    finding_partial_absence = CandidateFinding(
        id="CQ-TRAP-01",
        rule_id="Q_SEC_01",
        claim="No existe implementacion dentro de process_payment; la funcion termina abruptamente.",
        severity=FindingSeverity.high,
        evidence_ids=["c3d4e5f6"],
        commit_sha="c3d4e5f6",
        file_path="src/services/billing.py",
        line=88,
        confidence=0.85,
        claim_basis="visible_code",
    )
    s3 = BenchmarkSample(
        sample_id="SAMPLE-03-PARTIAL-ABSENCE",
        name="Absence Claim on Partial Diff",
        category="partial_absence_trap",
        ground_truth_is_defect=False,
        description="Hallucinated missing implementation asserted from partial diff line.",
        snippets=[snip_partial],
        rule=rule_security,
        candidate_finding=finding_partial_absence,
    )

    # 4. False Positive Trap: Inferred Lack of Tests from Missing CI Run
    snip_ci = CodeDiffSnippet(
        repository="HORMIGESTION",
        commit_sha="d4e5f6a7",
        file_path="src/models/user.py",
        lines=[10],
        content="class User(BaseModel): name: str",
        evidence_scope="diff",
        is_complete=False,
    )
    finding_ci_inference = CandidateFinding(
        id="CQ-TRAP-02",
        rule_id="Q_CI_01",
        claim="Coverage unavailable and missing tests detected; no ci run exists.",
        severity=FindingSeverity.medium,
        evidence_ids=["d4e5f6a7"],
        commit_sha="d4e5f6a7",
        file_path="src/models/user.py",
        line=10,
        confidence=0.75,
        claim_basis="visible_code",
    )
    s4 = BenchmarkSample(
        sample_id="SAMPLE-04-CI-INFERENCE",
        name="Inferred Absence from Missing CI",
        category="ci_coverage_trap",
        ground_truth_is_defect=False,
        description="Hallucinated absence of tests deduced solely from missing CI metric.",
        snippets=[snip_ci],
        rule=rule_coverage,
        candidate_finding=finding_ci_inference,
    )

    # 5. False Positive Trap: Out-of-bounds Line Citation
    snip_lines = CodeDiffSnippet(
        repository="HORMIGESTION",
        commit_sha="e5f6a7b8",
        file_path="src/utils/math.py",
        lines=[1, 2, 3],
        content="def add(a: int, b: int) -> int:\n    return a + b\n",
        evidence_scope="diff",
        is_complete=False,
    )
    finding_out_of_bounds = CandidateFinding(
        id="CQ-TRAP-03",
        rule_id="Q_SEC_01",
        claim="Unchecked division by zero at line 99.",
        severity=FindingSeverity.medium,
        evidence_ids=["e5f6a7b8"],
        commit_sha="e5f6a7b8",
        file_path="src/utils/math.py",
        line=99,
        confidence=0.80,
        claim_basis="visible_code",
    )
    s5 = BenchmarkSample(
        sample_id="SAMPLE-05-OUT-OF-BOUNDS",
        name="Out-of-bounds Line Number Citation",
        category="line_out_of_bounds_trap",
        ground_truth_is_defect=False,
        description="Hallucinated line number not present in evidence diff.",
        snippets=[snip_lines],
        rule=rule_security,
        candidate_finding=finding_out_of_bounds,
    )

    # 6. True Negative: Clean Code with Parameterized Query
    snip_clean = CodeDiffSnippet(
        repository="HORMIGESTION",
        commit_sha="f6a7b8c9",
        file_path="src/db/safe_orders.py",
        lines=[1],
        content="cursor.execute('SELECT * FROM orders WHERE id = %s', (order_id,))",
        evidence_scope="diff",
        is_complete=False,
    )
    s6 = BenchmarkSample(
        sample_id="SAMPLE-06-CLEAN",
        name="Safe Parameterized Query",
        category="clean_code",
        ground_truth_is_defect=False,
        description="Properly parameterized SQL query with no defect findings.",
        snippets=[snip_clean],
        rule=rule_security,
        candidate_finding=None,
    )

    return [s1, s2, s3, s4, s5, s6]


def run_quality_benchmark(
    samples: Optional[list[BenchmarkSample]] = None,
) -> QualityBenchmarkReport:
    """
    Executes the ground truth quality benchmark.
    Tests candidate findings through SemanticValidator and computes confusion matrix.
    """
    dataset = samples or get_standard_ground_truth_dataset()

    tp = 0
    fp = 0
    tn = 0
    fn = 0
    outcomes = []

    for sample in dataset:
        if sample.candidate_finding is None:
            # Clean code without finding -> True Negative
            tn += 1
            outcomes.append({
                "sample_id": sample.sample_id,
                "name": sample.name,
                "category": sample.category,
                "ground_truth": "clean",
                "validation_result": "no_finding_clean",
                "classification": "true_negative",
            })
            continue

        valid_rule_ids = {sample.rule.id}
        available_evidence = set(sample.candidate_finding.evidence_ids)
        errors = SemanticValidator.validate_candidate_findings(
            findings=[sample.candidate_finding],
            available_snippets=sample.snippets,
            valid_rule_ids=valid_rule_ids,
            available_evidence_ids=available_evidence,
            rules=[sample.rule],
            ci_result=sample.ci_result,
        )

        passed_validation = (len(errors) == 0)

        if sample.ground_truth_is_defect:
            if passed_validation:
                tp += 1
                classification = "true_positive"
            else:
                fn += 1
                classification = "false_negative"
        else:
            if passed_validation:
                fp += 1
                classification = "false_positive"
            else:
                tn += 1
                classification = "true_negative"

        outcomes.append({
            "sample_id": sample.sample_id,
            "name": sample.name,
            "category": sample.category,
            "ground_truth": "defect" if sample.ground_truth_is_defect else "non_defect",
            "passed_validation": passed_validation,
            "validation_errors": errors,
            "classification": classification,
        })

    total = len(dataset)
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 1.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 1.0
    specificity = (tn / (tn + fp)) if (tn + fp) > 0 else 1.0
    fpr = (fp / (fp + tn)) if (fp + tn) > 0 else 0.0
    fnr = (fn / (tp + fn)) if (tp + fn) > 0 else 0.0

    metrics = ConfusionMatrixMetrics(
        samples_total=total,
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        specificity=round(specificity, 4),
        false_positive_rate=round(fpr, 4),
        false_negative_rate=round(fnr, 4),
    )

    # CI Rules Evaluation check
    ci_rules = [
        QualityRule(id="Q1", description="CI tests passed", metric_key="tests_passed", source="ci", operator="==", threshold=True, mandatory=True),
        QualityRule(id="Q2", description="Coverage >= 80%", metric_key="coverage_percentage", source="ci", operator=">=", threshold=80.0, mandatory=True),
        QualityRule(id="Q3", description="0 Critical security findings", metric_key="open_critical_findings", source="review", operator="==", threshold=0, mandatory=True),
    ]

    ci_evaluations = []
    sample_facts = {"tests_passed": True, "coverage_percentage": 85.5, "open_critical_findings": 0}
    for rule in ci_rules:
        ev = evaluate_single_rule(rule, sample_facts)
        ci_evaluations.append({
            "rule_id": rule.id,
            "description": rule.description,
            "status": ev.status.value,
            "observed_value": ev.observed_value,
        })

    # Quality Gate criteria:
    # 1. 0 False Positives on protected traps (FPR == 0.0)
    # 2. Precision >= 95%
    # 3. Recall >= 95%
    passed_gate = (metrics.false_positives == 0 and metrics.precision >= 0.95 and metrics.recall >= 0.95)

    summary = (
        f"Quality Benchmark Completed: {total} samples evaluated. "
        f"Precision: {metrics.precision * 100:.1f}%, "
        f"Recall: {metrics.recall * 100:.1f}%, "
        f"FPR: {metrics.false_positive_rate * 100:.1f}%, "
        f"FNR: {metrics.false_negative_rate * 100:.1f}%. "
        f"Quality Gate: {'PASSED' if passed_gate else 'FAILED'}."
    )

    return QualityBenchmarkReport(
        metrics=metrics,
        sample_outcomes=outcomes,
        ci_rules_evaluated=ci_evaluations,
        passed_quality_gate=passed_gate,
        summary=summary,
    )
