from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.modules.llm_gateway.base import FakeLLMGateway, LLMGateway

from .triage import (
    EvalConfigurationError,
    apply_thresholds,
    evaluate,
    load_cases,
    load_thresholds,
    write_reports,
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[4]
    data_dir = repo_root / "data" / "evals" / "triage"
    parser = argparse.ArgumentParser(
        prog="eval_triage",
        description="Run the versioned, offline triage quality gate.",
    )
    parser.add_argument(
        "--provider",
        choices=("fake", "configured"),
        default="fake",
        help=(
            "fake is deterministic and free; configured requires effective "
            "non-fake LLM_* settings and a matching threshold profile"
        ),
    )
    parser.add_argument(
        "--expected-model",
        help=(
            "required with --provider configured; fails closed if the configured "
            "gateway resolves to a different model"
        ),
    )
    parser.add_argument("--runs", type=_positive_int, default=3)
    parser.add_argument("--cases", type=Path, default=data_dir / "cases.json")
    parser.add_argument("--holdout", type=Path, default=data_dir / "holdout_cases.json")
    parser.add_argument("--thresholds", type=Path, default=data_dir / "thresholds.json")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "artifacts" / "evals" / "triage",
    )
    return parser


def _gateway(provider: str) -> LLMGateway:
    if provider == "fake":
        return FakeLLMGateway()
    from app.modules.llm_gateway.factory import get_gateway

    gateway = get_gateway()
    if gateway.provider_name == "fake":
        raise EvalConfigurationError(
            "--provider configured resolved to Fake; configure a non-fake "
            "LLM_PROVIDER and LLM_API_KEY"
        )
    return gateway


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cases = load_cases([args.cases, args.holdout])
        thresholds = load_thresholds(args.thresholds)
        gateway = _gateway(args.provider)
        if args.provider == "configured" and not args.expected_model:
            raise EvalConfigurationError(
                "--provider configured requires --expected-model to pin the "
                "evaluation model"
            )
        if args.expected_model and gateway.model_name != args.expected_model:
            raise EvalConfigurationError(
                "configured evaluation model mismatch: "
                f"expected={args.expected_model}, effective={gateway.model_name}"
            )
        if thresholds.provider != gateway.provider_name:
            raise EvalConfigurationError(
                "threshold provider mismatch: "
                f"profile={thresholds.provider}, effective={gateway.provider_name}"
            )
        report = evaluate(cases, gateway=gateway, runs=args.runs)
        threshold_results = apply_thresholds(report, thresholds)
        json_path, markdown_path = write_reports(report, args.output_dir)
    except EvalConfigurationError as exc:
        print(f"evaluation configuration error: {exc}", file=sys.stderr)
        return 2

    passed = all(item["passed"] for item in threshold_results)
    print(f"triage evaluation: {'PASS' if passed else 'FAIL'}")
    print(
        "effective provider/model: "
        f"{report['versions']['provider']}/{report['versions']['model']}"
    )
    print(f"json report: {json_path}")
    print(f"markdown report: {markdown_path}")
    if not passed:
        for item in threshold_results:
            if not item["passed"]:
                print(
                    f"failed: {item['metric']} actual={item['actual']} "
                    f"{item['comparator']}={item['target']}",
                    file=sys.stderr,
                )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
