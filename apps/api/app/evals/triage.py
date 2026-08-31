from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from app.models.tickets import TicketPriority
from app.modules.llm_gateway.base import (
    TRIAGE_SCHEMA_VERSION,
    TriageGateway,
    enforce_triage_policy,
    generate_triage_safe,
)
from app.modules.llm_gateway.schemas import Intent, RiskLevel, Route, TriageResult

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
EVALUATOR_VERSION = "triage-evaluator-v2"

# These fields determine routing and policy. Summary prose and reason codes are
# retained for review, but wording alone cannot prove a policy or identity gap.
COMPARISON_FIELDS = (
    "intent",
    "risk_level",
    "route",
    "suggested_priority",
    "missing_information",
    "needs_human_review",
)

MINIMUM_METRICS = frozenset(
    {
        "case_pass_rate",
        "intent_accuracy",
        "risk_accuracy",
        "schema_validity_rate",
        "human_review_accuracy",
        "R3_recall",
        "safe_fallback_rate",
        "identity_invariance_rate",
    }
)
MAXIMUM_METRICS = frozenset(
    {
        "volatility_rate",
        "normal_fallback_rate",
        "p95_latency_ms",
        "max_required_override_rate",
    }
)
SUPPORTED_THRESHOLD_METRICS = MINIMUM_METRICS | MAXIMUM_METRICS


class EvalConfigurationError(ValueError):
    """Evaluation input or threshold configuration is not usable."""


class EvalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_level: ShortText = "unspecified"
    region: ShortText | None = None
    language: ShortText | None = None


class InvarianceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: ShortText | None = None
    expectation: Literal["same_output"] = "same_output"


class ExpectedTriage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    risk_level: RiskLevel
    route: Route
    suggested_priority: Literal["low", "normal", "high", "urgent"] | None = None
    summary: str | None = None
    missing_information: list[ShortText] = Field(default_factory=list, max_length=20)
    needs_human_review: bool = False


class EvalCase(BaseModel):
    """Versioned, synthetic evaluation input. No ticket/database identifier exists."""

    model_config = ConfigDict(extra="forbid")

    id: ShortText
    text: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
    expected: ExpectedTriage
    context: EvalContext = Field(default_factory=EvalContext)
    slice_id: ShortText | None = None
    invariance: InvarianceSpec | ShortText | bool | None = None
    category: ShortText | None = None
    rationale: (
        Annotated[str, StringConstraints(min_length=1, max_length=2_000)] | None
    ) = None
    split: Literal["public", "holdout"] | None = None
    tags: list[ShortText] = Field(default_factory=list, max_length=50)


class ThresholdBound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float | None = None
    max: float | None = None


class ThresholdConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: ShortText
    provider: ShortText
    metrics: dict[str, float | ThresholdBound]


class GatewayMetadata(Protocol):
    provider_name: str
    model_name: str
    prompt_version: str


class _ObservedGateway:
    """Observe provider failures while preserving the production safe-call behavior."""

    def __init__(self, gateway: TriageGateway):
        self._gateway = gateway
        self.provider_name = gateway.provider_name
        self.model_name = gateway.model_name
        self.prompt_version = gateway.prompt_version
        self.raw_schema_valid: bool | None = None
        self.error_kind: str | None = None

    def generate_structured(self, *, text: str) -> TriageResult:
        try:
            raw = self._gateway.generate_structured(text=text)
            result = TriageResult.model_validate(raw)
        except Exception as exc:
            self.error_kind = _exception_kind(exc)
            self.raw_schema_valid = (
                False if self.error_kind == "invalid_provider_output" else None
            )
            raise
        self.raw_schema_valid = True
        return result


class _FaultGateway:
    provider_name = "fault-injection"
    model_name = "synthetic-fault-v1"
    prompt_version = "triage-v1"

    def __init__(self, kind: Literal["invalid", "timeout", "rate_limit"]):
        self.kind = kind

    def generate_structured(self, *, text: str) -> TriageResult:
        del text
        if self.kind == "timeout":
            raise TimeoutError("synthetic timeout")
        if self.kind == "rate_limit":
            raise _SyntheticRateLimitError("synthetic rate limit")
        return {"unexpected": "shape"}  # type: ignore[return-value]


class _SyntheticRateLimitError(RuntimeError):
    pass


def load_cases(paths: Sequence[Path]) -> list[EvalCase]:
    adapter = TypeAdapter(list[EvalCase])
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for source_index, path in enumerate(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = adapter.validate_python(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise EvalConfigurationError(f"invalid case file {path}: {exc}") from exc
        if not loaded:
            raise EvalConfigurationError(f"case file {path} must not be empty")
        for case in loaded:
            if case.id in seen_ids:
                raise EvalConfigurationError(f"duplicate case id: {case.id}")
            seen_ids.add(case.id)
            inferred_split = "public" if source_index == 0 else "holdout"
            cases.append(
                case
                if case.split is not None
                else case.model_copy(update={"split": inferred_split})
            )
    if not cases:
        raise EvalConfigurationError("evaluation needs at least one case")
    public_texts = {case.text.strip() for case in cases if case.split == "public"}
    holdout_texts = {case.text.strip() for case in cases if case.split == "holdout"}
    if not public_texts or not holdout_texts:
        raise EvalConfigurationError(
            "evaluation needs non-empty public and holdout splits"
        )
    overlap = public_texts & holdout_texts
    if overlap:
        raise EvalConfigurationError(
            f"public and holdout text overlap detected ({len(overlap)} unique text(s))"
        )
    return sorted(cases, key=lambda item: item.id)


def load_thresholds(path: Path) -> ThresholdConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "metrics" not in payload:
            payload = {
                "schema_version": "triage-thresholds-v1",
                "provider": "fake",
                "metrics": payload,
            }
        config = ThresholdConfig.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise EvalConfigurationError(f"invalid thresholds file {path}: {exc}") from exc

    unknown = sorted(set(config.metrics) - SUPPORTED_THRESHOLD_METRICS)
    if unknown:
        raise EvalConfigurationError(f"unsupported threshold metrics: {unknown}")
    missing = sorted(SUPPORTED_THRESHOLD_METRICS - set(config.metrics))
    if missing:
        raise EvalConfigurationError(f"missing required threshold metrics: {missing}")
    for metric, value in config.metrics.items():
        _threshold_rule(metric, value)
    return config


def evaluate(
    cases: Sequence[EvalCase],
    *,
    gateway: TriageGateway,
    runs: int = 3,
    timer: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    if runs < 1:
        raise EvalConfigurationError("runs must be at least 1")

    case_reports = [
        _evaluate_case(case, gateway=gateway, runs=runs, timer=timer)
        for case in sorted(cases, key=lambda item: item.id)
    ]
    fault_reports = _evaluate_fault_injections(timer=timer)
    aggregate = _aggregate(case_reports, fault_reports)
    slice_metrics = _verification_slices(case_reports, fault_reports)
    invariance = _identity_invariance(case_reports)
    aggregate["identity_invariance_rate"] = invariance["rate"]

    return {
        "report_schema_version": "triage-eval-report-v1",
        "evaluation_contract": {
            "pipeline": ["generate_triage_safe", "enforce_triage_policy"],
            "required_override_rate_definition": (
                "Fraction of normal evaluation runs whose final output differs from "
                "ground truth; it is not an observed agent acceptance rate."
            ),
            "safe_fallback_rate_definition": (
                "Fraction of isolated invalid/timeout/rate-limit injections that "
                "produce the conservative R3 human-handoff fallback."
            ),
            "threshold_comparators": {
                "minimum": sorted(MINIMUM_METRICS),
                "maximum": sorted(MAXIMUM_METRICS),
                "max_required_override_rate": "compares required_override_rate",
            },
            "output_comparison_fields": list(COMPARISON_FIELDS),
            "evidence_boundaries": [
                "Synthetic repository cases do not establish production-model "
                "quality, fairness, or novel-attack robustness.",
                "Repository holdout is process-isolated but is not secret, blind, "
                "or contamination-proof.",
                "Offline evaluation does not validate UI controls; override and "
                "review UX require E2E and human acceptance testing.",
                "Provider cost and token claims require provider telemetry.",
            ],
        },
        "versions": {
            "provider": gateway.provider_name,
            "model": gateway.model_name,
            "prompt": gateway.prompt_version,
            "prompt_sha256": getattr(gateway, "prompt_sha256", None),
            "schema": TRIAGE_SCHEMA_VERSION,
            "evaluator": EVALUATOR_VERSION,
        },
        "inputs": _input_metadata(cases),
        "runs_per_case": runs,
        "cost": _cost_metadata(gateway),
        "aggregate": aggregate,
        "verification_level_slices": slice_metrics,
        "diagnostic_slices": _diagnostic_slices(case_reports),
        "identity_invariance": invariance,
        "fault_injections": fault_reports,
        "cases": case_reports,
    }


def apply_thresholds(
    report: dict[str, Any], config: ThresholdConfig
) -> list[dict[str, Any]]:
    effective_provider = str(report["versions"]["provider"])
    if config.provider != effective_provider:
        raise EvalConfigurationError(
            "threshold provider mismatch: "
            f"profile={config.provider}, effective={effective_provider}"
        )
    aggregate = report["aggregate"]
    results: list[dict[str, Any]] = []
    for threshold_name in sorted(config.metrics):
        metric_name = (
            "required_override_rate"
            if threshold_name == "max_required_override_rate"
            else threshold_name
        )
        actual = float(aggregate[metric_name])
        comparator, target = _threshold_rule(
            threshold_name, config.metrics[threshold_name]
        )
        passed = actual >= target if comparator == "min" else actual <= target
        results.append(
            {
                "threshold": threshold_name,
                "metric": metric_name,
                "comparator": comparator,
                "target": target,
                "actual": actual,
                "passed": passed,
            }
        )
    report["thresholds"] = {
        "schema_version": config.schema_version,
        "config_fingerprint_sha256": _sha256_json(config.model_dump(mode="json")),
        "passed": all(item["passed"] for item in results),
        "results": results,
    }
    return results


def write_reports(report: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    versions = report["versions"]
    json_text = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    )
    report_fingerprint = hashlib.sha256(json_text.encode("utf-8")).hexdigest()
    suffix = "-".join(
        _slug(str(versions[key]))
        for key in ("provider", "model", "prompt", "schema", "evaluator")
    )
    threshold_fingerprint = report.get("thresholds", {}).get(
        "config_fingerprint_sha256", "unthresholded"
    )
    prompt_fingerprint = versions.get("prompt_sha256") or "not-applicable"
    stem = (
        f"triage-eval-{suffix}"
        f"-prompt-{str(prompt_fingerprint)[:12]}"
        f"-cases-{report['inputs']['case_fingerprint_sha256'][:12]}"
        f"-thresholds-{str(threshold_fingerprint)[:12]}"
        f"-runs-{report['runs_per_case']}"
        f"-report-{report_fingerprint[:16]}"
    )
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(f"{json_text}\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _evaluate_case(
    case: EvalCase,
    *,
    gateway: TriageGateway,
    runs: int,
    timer: Callable[[], float],
) -> dict[str, Any]:
    run_reports: list[dict[str, Any]] = []
    outputs: list[str] = []
    for run_index in range(1, runs + 1):
        observed = _ObservedGateway(gateway)
        started = timer()
        pre_policy_result = generate_triage_safe(observed, text=case.text)
        result = enforce_triage_policy(pre_policy_result, text=case.text)
        latency_ms = max(0.0, round((timer() - started) * 1000, 3))
        final_schema_valid = _is_schema_valid(result)
        safe_fallback = _is_safe_fallback(result)
        pre_policy_checks = _checks(
            case.expected,
            pre_policy_result,
            raw_schema_valid=observed.raw_schema_valid,
            final_schema_valid=_is_schema_valid(pre_policy_result),
            safe_fallback=safe_fallback,
        )
        checks = _checks(
            case.expected,
            result,
            raw_schema_valid=observed.raw_schema_valid,
            final_schema_valid=final_schema_valid,
            safe_fallback=safe_fallback,
        )
        output = result.model_dump(mode="json")
        canonical_output = _comparison_signature(output)
        outputs.append(canonical_output)
        failures = _failure_classifications(
            checks=checks,
            safe_fallback=safe_fallback,
            error_kind=observed.error_kind,
            expected_risk=case.expected.risk_level,
        )
        run_reports.append(
            {
                "run": run_index,
                "latency_ms": latency_ms,
                "raw_schema_valid": observed.raw_schema_valid,
                "final_schema_valid": final_schema_valid,
                "safe_fallback": safe_fallback,
                "provider_error_kind": observed.error_kind,
                "pre_policy_checks": pre_policy_checks,
                "checks": checks,
                "passed": all(value is True for value in checks.values()),
                "policy_floor_applied": result != pre_policy_result,
                "pre_policy_output": pre_policy_result.model_dump(mode="json"),
                "output": output,
                "versions": {
                    "provider": gateway.provider_name,
                    "model": gateway.model_name,
                    "prompt": gateway.prompt_version,
                    "prompt_sha256": getattr(gateway, "prompt_sha256", None),
                    "schema": TRIAGE_SCHEMA_VERSION,
                    "evaluator": EVALUATOR_VERSION,
                },
                "failures": failures,
            }
        )

    distinct_outputs = len(set(outputs))
    volatile = distinct_outputs > 1
    failures = _deduplicate_failures(
        failure for run in run_reports for failure in run["failures"]
    )
    if volatile:
        failures = _deduplicate_failures(
            [
                *failures,
                {
                    "classification": "volatility",
                    "candidate_remediation_area": "provider",
                },
            ]
        )
    return {
        "id": case.id,
        "split": case.split or "unspecified",
        "slice_id": case.slice_id,
        "category": case.category,
        "tags": sorted(case.tags),
        "verification_level": case.context.verification_level,
        "context": case.context.model_dump(mode="json"),
        "invariance_group": _invariance_group(case),
        "expected": case.expected.model_dump(mode="json"),
        "runs": run_reports,
        "distinct_output_count": distinct_outputs,
        "volatile": volatile,
        "passed": all(run["passed"] for run in run_reports) and not volatile,
        "failures": failures,
    }


def _checks(
    expected: ExpectedTriage,
    result: TriageResult,
    *,
    raw_schema_valid: bool | None,
    final_schema_valid: bool,
    safe_fallback: bool,
) -> dict[str, bool | None]:
    checks = {
        "intent": result.intent is expected.intent,
        "risk": result.risk_level is expected.risk_level,
        "route": result.route is expected.route,
        "human_review": result.needs_human_review is expected.needs_human_review,
        "missing_information": result.missing_information
        == expected.missing_information,
        "schema": raw_schema_valid,
        "final_schema": final_schema_valid,
        "no_fallback": not safe_fallback,
    }
    if expected.suggested_priority is not None:
        checks["suggested_priority"] = (
            result.suggested_priority.value == expected.suggested_priority
        )
    if expected.summary is not None:
        checks["summary"] = result.summary == expected.summary
    return checks


def _failure_classifications(
    *,
    checks: Mapping[str, bool | None],
    safe_fallback: bool,
    error_kind: str | None,
    expected_risk: RiskLevel,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    mapping = {
        "intent": ("intent_mismatch", "prompt"),
        "risk": ("risk_mismatch", "rule"),
        "route": ("route_or_priority_mismatch", "rule"),
        "human_review": ("human_review_mismatch", "rule"),
        "missing_information": ("missing_information_mismatch", "data"),
        "schema": ("schema_invalid", "provider"),
        "final_schema": ("unsafe_fallback", "provider"),
        "suggested_priority": ("route_or_priority_mismatch", "rule"),
        "summary": ("summary_mismatch", "prompt"),
    }
    for check, (classification, owner) in mapping.items():
        if checks.get(check) is False:
            failures.append(
                {
                    "classification": classification,
                    "candidate_remediation_area": owner,
                }
            )
    if safe_fallback and error_kind is not None:
        failures.append(
            {
                "classification": error_kind,
                "candidate_remediation_area": "provider",
            }
        )
    if expected_risk is RiskLevel.R3 and checks.get("risk") is False:
        failures.append(
            {"classification": "r3_miss", "candidate_remediation_area": "rule"}
        )
    if any(
        checks.get(check) is False
        for check in (
            "intent",
            "risk",
            "route",
            "human_review",
            "missing_information",
            "suggested_priority",
            "summary",
        )
    ):
        failures.append(
            {
                "classification": "required_override",
                "candidate_remediation_area": "ui",
            }
        )
    return _deduplicate_failures(failures)


def _aggregate(
    case_reports: Sequence[Mapping[str, Any]],
    fault_reports: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    runs = [run for case in case_reports for run in case["runs"]]
    if not runs:
        raise EvalConfigurationError("evaluation produced no runs")

    def rate(predicate: Callable[[Mapping[str, Any]], bool]) -> float:
        return _rounded(sum(predicate(run) for run in runs) / len(runs))

    r3_runs = [
        run
        for case in case_reports
        if case["expected"]["risk_level"] == RiskLevel.R3.value
        for run in case["runs"]
    ]
    schema_observations = [run for run in runs if run["raw_schema_valid"] is not None]
    latencies = sorted(float(run["latency_ms"]) for run in runs)
    required_override_rate = rate(lambda run: not _ground_truth_pass(run))
    return {
        "case_pass_rate": _rounded(
            sum(bool(case["passed"]) for case in case_reports) / len(case_reports)
        ),
        "intent_accuracy": rate(lambda run: bool(run["checks"]["intent"])),
        "pre_policy_intent_accuracy": rate(
            lambda run: bool(run["pre_policy_checks"]["intent"])
        ),
        "risk_accuracy": rate(lambda run: bool(run["checks"]["risk"])),
        "pre_policy_risk_accuracy": rate(
            lambda run: bool(run["pre_policy_checks"]["risk"])
        ),
        "schema_validity_rate": (
            _rounded(
                sum(run["raw_schema_valid"] is True for run in schema_observations)
                / len(schema_observations)
            )
            if schema_observations
            else 0.0
        ),
        "final_schema_validity_rate": rate(lambda run: bool(run["final_schema_valid"])),
        "human_review_accuracy": rate(lambda run: bool(run["checks"]["human_review"])),
        "R3_recall": (
            _rounded(
                sum(
                    run["output"]["risk_level"] == RiskLevel.R3.value for run in r3_runs
                )
                / len(r3_runs)
            )
            if r3_runs
            else 0.0
        ),
        "pre_policy_R3_recall": (
            _rounded(
                sum(
                    run["pre_policy_output"]["risk_level"] == RiskLevel.R3.value
                    for run in r3_runs
                )
                / len(r3_runs)
            )
            if r3_runs
            else 0.0
        ),
        "safe_fallback_rate": _rounded(
            sum(bool(item["passed"]) for item in fault_reports) / len(fault_reports)
        ),
        "normal_fallback_rate": rate(lambda run: bool(run["safe_fallback"])),
        "policy_floor_applied_rate": rate(
            lambda run: bool(run["policy_floor_applied"])
        ),
        "volatility_rate": _rounded(
            sum(bool(case["volatile"]) for case in case_reports) / len(case_reports)
        ),
        "required_override_rate": required_override_rate,
        "p95_latency_ms": _nearest_rank_percentile(latencies, 0.95),
    }


def _verification_slices(
    case_reports: Sequence[Mapping[str, Any]],
    fault_reports: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in case_reports:
        grouped[str(case["verification_level"])].append(case)
    return {
        level: _aggregate(grouped[level], fault_reports) for level in sorted(grouped)
    }


def _diagnostic_slices(
    case_reports: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, float | int]]]:
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for case in case_reports:
        grouped["split"][str(case["split"])].append(case)
        grouped["intent"][str(case["expected"]["intent"])].append(case)
        grouped["risk"][str(case["expected"]["risk_level"])].append(case)
        language = _case_language(case)
        grouped["language"][language].append(case)
        tags = case["tags"] or ["untagged"]
        for tag in tags:
            grouped["tag"][str(tag)].append(case)
    return {
        dimension: {
            value: _diagnostic_slice_metrics(groups[value]) for value in sorted(groups)
        }
        for dimension, groups in sorted(grouped.items())
    }


def _diagnostic_slice_metrics(
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, float | int]:
    runs = [run for case in cases for run in case["runs"]]

    def run_rate(check: Callable[[Mapping[str, Any]], bool]) -> float:
        return _rounded(sum(check(run) for run in runs) / len(runs))

    return {
        "case_count": len(cases),
        "run_count": len(runs),
        "case_pass_rate": _rounded(
            sum(bool(case["passed"]) for case in cases) / len(cases)
        ),
        "intent_accuracy": run_rate(lambda run: run["checks"]["intent"] is True),
        "risk_accuracy": run_rate(lambda run: run["checks"]["risk"] is True),
        "human_review_accuracy": run_rate(
            lambda run: run["checks"]["human_review"] is True
        ),
        "normal_fallback_rate": run_rate(lambda run: bool(run["safe_fallback"])),
        "required_override_rate": run_rate(lambda run: not _ground_truth_pass(run)),
    }


def _case_language(case: Mapping[str, Any]) -> str:
    configured = case["context"].get("language")
    if configured:
        return str(configured)
    language_tags = {"zh-CN", "en-US", "mixed-language"}
    for tag in case["tags"]:
        if tag in language_tags:
            return str(tag)
    return "unspecified"


def _comparison_signature(output: Mapping[str, Any]) -> str:
    return json.dumps(
        {field: output[field] for field in COMPARISON_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _identity_invariance(
    case_reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in case_reports:
        group = case["invariance_group"]
        if group is not None:
            groups[str(group)].append(case)

    comparisons: list[dict[str, Any]] = []
    for group_id in sorted(groups):
        cases = sorted(groups[group_id], key=lambda item: str(item["id"]))
        levels = {str(case["verification_level"]) for case in cases}
        if len(cases) < 2 or len(levels) < 2:
            continue
        run_count = min(len(case["runs"]) for case in cases)
        for run_index in range(run_count):
            outputs = [
                _comparison_signature(case["runs"][run_index]["output"])
                for case in cases
            ]
            passed = len(set(outputs)) == 1
            comparisons.append(
                {
                    "group_id": group_id,
                    "run": run_index + 1,
                    "case_ids": [case["id"] for case in cases],
                    "verification_levels": sorted(levels),
                    "passed": passed,
                    "failure": (
                        None
                        if passed
                        else {
                            "classification": "identity_variance",
                            "candidate_remediation_area": "rule",
                        }
                    ),
                }
            )
    rate = (
        _rounded(sum(bool(item["passed"]) for item in comparisons) / len(comparisons))
        if comparisons
        else 0.0
    )
    return {
        "rate": rate,
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
    }


def _evaluate_fault_injections(*, timer: Callable[[], float]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    fault_kinds: tuple[Literal["invalid", "timeout", "rate_limit"], ...] = (
        "invalid",
        "rate_limit",
        "timeout",
    )
    for kind in fault_kinds:
        gateway = _FaultGateway(kind)
        observed = _ObservedGateway(gateway)
        started = timer()
        result = generate_triage_safe(observed, text="synthetic fault probe")
        result = enforce_triage_policy(result, text="synthetic fault probe")
        latency_ms = max(0.0, round((timer() - started) * 1000, 3))
        passed = _is_safe_fallback(result) and _is_schema_valid(result)
        reports.append(
            {
                "fault": kind,
                "latency_ms": latency_ms,
                "observed_error_kind": observed.error_kind,
                "raw_schema_valid": observed.raw_schema_valid,
                "final_schema_valid": _is_schema_valid(result),
                "safe_fallback": _is_safe_fallback(result),
                "passed": passed,
                "output": result.model_dump(mode="json"),
                "failure": (
                    None
                    if passed
                    else {
                        "classification": "unsafe_fault_handling",
                        "candidate_remediation_area": "provider",
                    }
                ),
            }
        )
    return reports


def _is_schema_valid(result: TriageResult) -> bool:
    try:
        TriageResult.model_validate(result.model_dump())
    except ValueError:
        return False
    return True


def _is_safe_fallback(result: TriageResult) -> bool:
    return (
        result.intent is Intent.unknown
        and result.risk_level is RiskLevel.R3
        and result.route is Route.human_handoff
        and result.suggested_priority is TicketPriority.urgent
        and result.needs_human_review
        and "schema_validation_failed" in result.reason_codes
    )


def _exception_kind(exc: Exception) -> str:
    current: BaseException | None = exc
    seen: set[int] = set()
    invalid_output = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__.lower()
        if isinstance(current, TimeoutError) or "timeout" in name:
            return "provider_timeout"
        if "rate" in name and "limit" in name:
            return "provider_rate_limit"
        invalid_output = invalid_output or isinstance(current, (ValueError, TypeError))
        current = current.__cause__ or current.__context__
    if invalid_output:
        return "invalid_provider_output"
    return "provider_error"


def _ground_truth_pass(run: Mapping[str, Any]) -> bool:
    return all(
        bool(value)
        for check, value in run["checks"].items()
        if check not in {"schema", "final_schema", "no_fallback"}
    )


def _threshold_rule(
    metric: str, configured: float | ThresholdBound
) -> tuple[Literal["min", "max"], float]:
    expected_comparator: Literal["min", "max"] = (
        "min" if metric in MINIMUM_METRICS else "max"
    )
    if isinstance(configured, ThresholdBound):
        target = configured.min if expected_comparator == "min" else configured.max
        opposite = configured.max if expected_comparator == "min" else configured.min
        if target is None or opposite is not None:
            raise EvalConfigurationError(
                f"threshold {metric} must define only {expected_comparator}"
            )
    else:
        target = configured
    if not math.isfinite(target) or target < 0:
        raise EvalConfigurationError(f"invalid threshold {metric}: {target}")
    if metric not in {"p95_latency_ms"} and target > 1:
        raise EvalConfigurationError(f"rate threshold {metric} must be <= 1")
    return expected_comparator, float(target)


def _invariance_group(case: EvalCase) -> str | None:
    if case.invariance is False:
        return None
    if isinstance(case.invariance, InvarianceSpec) and case.invariance.group_id:
        return case.invariance.group_id
    if isinstance(case.invariance, str):
        return case.invariance
    if case.invariance is True or case.context.verification_level != "unspecified":
        return f"text:{case.text}"
    return None


def _input_metadata(cases: Sequence[EvalCase]) -> dict[str, Any]:
    canonical_cases = [
        case.model_dump(mode="json") for case in sorted(cases, key=lambda c: c.id)
    ]
    split_counts = {
        split: sum(case.split == split for case in cases)
        for split in ("public", "holdout")
    }
    return {
        "case_count": len(cases),
        "unique_text_count": len({case.text for case in cases}),
        "split_counts": split_counts,
        "case_fingerprint_sha256": _sha256_json(canonical_cases),
    }


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _cost_metadata(gateway: GatewayMetadata) -> dict[str, Any]:
    if gateway.provider_name == "fake":
        return {
            "status": "measured",
            "currency": "USD",
            "amount": 0,
            "tokens": 0,
        }
    return {
        "status": "unavailable",
        "amount": None,
        "tokens": None,
        "reason": "Provider token usage and cost metadata are not collected.",
    }


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return round(float(values[index]), 3)


def _rounded(value: float) -> float:
    return round(value, 6)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned[:80] or "unknown"


def _deduplicate_failures(
    failures: Iterable[Mapping[str, str]],
) -> list[dict[str, str]]:
    unique = {
        (str(item["classification"]), str(item["candidate_remediation_area"]))
        for item in failures
    }
    return [
        {
            "classification": classification,
            "candidate_remediation_area": area,
        }
        for classification, area in sorted(unique)
    ]


def _render_markdown(report: Mapping[str, Any]) -> str:
    versions = report["versions"]
    aggregate = report["aggregate"]
    inputs = report["inputs"]
    lines = [
        "# Triage evaluation report",
        "",
        (
            f"Provider/model: `{versions['provider']}` / `{versions['model']}`\n"
            f"\nPrompt/schema: `{versions['prompt']}` / `{versions['schema']}`"
        ),
        f"Evaluator: `{versions['evaluator']}`",
        _markdown_prompt_hash(versions),
        f"Runs per case: **{report['runs_per_case']}**",
        _markdown_cost(report["cost"]),
        (
            f"Cases: **{inputs['case_count']}** "
            f"(**{inputs['unique_text_count']}** unique texts; "
            f"public **{inputs['split_counts']['public']}**, "
            f"holdout **{inputs['split_counts']['holdout']}**)"
        ),
        f"Case fingerprint: `{inputs['case_fingerprint_sha256']}`",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for metric in sorted(aggregate):
        lines.append(f"| `{metric}` | {aggregate[metric]} |")

    thresholds = report.get("thresholds")
    if thresholds is not None:
        lines.extend(
            [
                "",
                "## Threshold gate",
                "",
                f"Overall: **{'PASS' if thresholds['passed'] else 'FAIL'}**",
                f"Threshold fingerprint: `{thresholds['config_fingerprint_sha256']}`",
                "",
                "| Metric | Rule | Actual | Result |",
                "|---|---:|---:|---|",
            ]
        )
        for item in thresholds["results"]:
            lines.append(
                f"| `{item['metric']}` | {item['comparator']} "
                f"{item['target']} | {item['actual']} | "
                f"{'PASS' if item['passed'] else 'FAIL'} |"
            )

    lines.extend(
        [
            "",
            "## Verification-level slices",
            "",
            "| Verification | Cases pass | Intent | Risk | Human review | Fallback |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for level, metrics in report["verification_level_slices"].items():
        lines.append(
            f"| `{level}` | {metrics['case_pass_rate']} | "
            f"{metrics['intent_accuracy']} | {metrics['risk_accuracy']} | "
            f"{metrics['human_review_accuracy']} | "
            f"{metrics['normal_fallback_rate']} |"
        )

    lines.extend(
        [
            "",
            "## Diagnostic slices",
            "",
            "| Dimension | Value | Cases | Runs | Case pass | Intent | Risk | "
            "Human review | Fallback | Required override |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for dimension, values in report["diagnostic_slices"].items():
        for value, metrics in values.items():
            lines.append(
                f"| `{dimension}` | `{value}` | {metrics['case_count']} | "
                f"{metrics['run_count']} | {metrics['case_pass_rate']} | "
                f"{metrics['intent_accuracy']} | {metrics['risk_accuracy']} | "
                f"{metrics['human_review_accuracy']} | "
                f"{metrics['normal_fallback_rate']} | "
                f"{metrics['required_override_rate']} |"
            )

    identity = report["identity_invariance"]
    lines.extend(
        [
            "",
            "## Identity invariance",
            "",
            f"Rate: **{identity['rate']}** across "
            f"**{identity['comparison_count']}** repeated comparisons.",
            "",
            "| Group | Run | Verification levels | Result | Candidate area |",
            "|---|---:|---|---|---|",
        ]
    )
    for item in identity["comparisons"]:
        failure = item["failure"]
        remediation = (
            "-"
            if failure is None
            else (
                f"{failure['classification']}→{failure['candidate_remediation_area']}"
            )
        )
        lines.append(
            f"| `{item['group_id']}` | {item['run']} | "
            f"{', '.join(item['verification_levels'])} | "
            f"{'PASS' if item['passed'] else 'FAIL'} | {remediation} |"
        )

    lines.extend(
        [
            "",
            "## Fault injection",
            "",
            "| Fault | Error classification | Raw schema | Final schema | "
            "Safe fallback | Result |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in report["fault_injections"]:
        lines.append(
            f"| `{item['fault']}` | `{item['observed_error_kind']}` | "
            f"{_schema_label(item['raw_schema_valid'])} | "
            f"{'valid' if item['final_schema_valid'] else 'invalid'} | "
            f"{'yes' if item['safe_fallback'] else 'no'} | "
            f"{'PASS' if item['passed'] else 'FAIL'} |"
        )

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Split | Intent | Risk | Language | Verification | Pass | "
            "Volatile | Failures → candidate area |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for case in report["cases"]:
        failures = ", ".join(
            f"{item['classification']}→{item['candidate_remediation_area']}"
            for item in case["failures"]
        )
        lines.append(
            f"| `{case['id']}` | `{case['split']}` | "
            f"`{case['expected']['intent']}` | `{case['expected']['risk_level']}` | "
            f"`{_case_language(case)}` | "
            f"`{case['verification_level']}` | "
            f"{'PASS' if case['passed'] else 'FAIL'} | "
            f"{'yes' if case['volatile'] else 'no'} | {failures or '-'} |"
        )

    failure_counts: dict[tuple[str, str], int] = defaultdict(int)
    for case in report["cases"]:
        for failure in case["failures"]:
            failure_counts[
                (
                    failure["classification"],
                    failure["candidate_remediation_area"],
                )
            ] += 1
    lines.extend(
        [
            "",
            "## Failure classification and remediation",
            "",
            "| Classification | Candidate remediation area | Affected cases |",
            "|---|---|---:|",
        ]
    )
    if failure_counts:
        for (classification, owner), count in sorted(failure_counts.items()):
            lines.append(f"| `{classification}` | `{owner}` | {count} |")
    else:
        lines.append("| none | - | 0 |")

    lines.extend(
        [
            "",
            "### Remediation guide",
            "",
            "- `intent_mismatch` / `summary_mismatch`: review the Prompt and "
            "representative examples before adding brittle keyword rules.",
            "- `missing_information_mismatch`: review annotation quality and "
            "coverage in the evaluation data.",
            "- `risk_mismatch`, `r3_miss`, or route/priority/review mismatch: "
            "repair deterministic business rules and their tests first.",
            "- `required_override`: the UI must preserve an explicit human "
            "accept/edit/reject path; offline evaluation cannot prove that UX.",
            "- Schema, timeout, rate-limit, or volatility failures: repair the "
            "provider gateway, parsing, retry, or fallback boundary.",
        ]
    )

    lines.extend(
        [
            "",
            "## Interpretation notes",
            "",
            "- `required_override_rate` is inferred from ground-truth mismatches; "
            "it is not an observed agent acceptance rate.",
            "- Candidate remediation areas are triage hints, not proven root causes "
            "or ownership assignments; a human must diagnose each failure.",
            "- Fault injection is isolated from normal-case accuracy. Its "
            "`safe_fallback_rate` covers invalid output, timeout, and rate limiting.",
            "- Fake provider cost is exactly USD 0. Real-provider cost and tokens are "
            "reported as unavailable unless collected.",
            "- This synthetic repository holdout is not secret, blind, statistically "
            "independent, or contamination-proof.",
            "- Offline results do not prove the UI override/review controls; those "
            "require E2E and human acceptance testing.",
            "- Identity-invariance pairs are separate cases and therefore have "
            "separate weight in aggregate rates; unique-text counts are shown above.",
            "- A pass does not establish production quality, fairness, robustness, "
            "or enterprise readiness.",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_cost(cost: Mapping[str, Any]) -> str:
    if cost["status"] == "measured":
        return (
            f"Cost: **{cost['currency']} {cost['amount']}**; "
            f"tokens: **{cost['tokens']}**"
        )
    return "Cost/tokens: **unavailable** (provider telemetry is not collected)"


def _markdown_prompt_hash(versions: Mapping[str, Any]) -> str:
    prompt_hash = versions.get("prompt_sha256")
    if prompt_hash:
        return f"Prompt SHA-256: `{prompt_hash}`"
    return "Prompt SHA-256: **not applicable/unavailable for this gateway**"


def _schema_label(value: bool | None) -> str:
    if value is None:
        return "no output"
    return "valid" if value else "invalid"
