"""CLI for the isolated database-backed RAG quality gate."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.llm_gateway.base import CitationAnswerGateway, FakeLLMGateway
from app.seed import seed
from app.shared.config import get_settings

from .rag import (
    RagEvalConfigurationError,
    apply_thresholds,
    load_cases,
    load_corpus,
    load_thresholds,
    run_evaluation,
    write_reports,
)


def _positive_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 20:
        raise argparse.ArgumentTypeError("must be between 1 and 20")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[4]
    data_dir = repo_root / "data" / "evals" / "rag"
    parser = argparse.ArgumentParser(
        prog="eval_rag",
        description=(
            "Run the synthetic database-backed RAG quality gate on an isolated "
            "local *_test database."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=("fake", "configured"),
        default="fake",
        help=(
            "fake is deterministic and offline; configured requires a non-fake "
            "LLM gateway and a matching provider threshold profile"
        ),
    )
    parser.add_argument(
        "--expected-model",
        help=(
            "required with --provider configured; fails closed if the configured "
            "gateway resolves to a different model"
        ),
    )
    database_group = parser.add_mutually_exclusive_group()
    database_group.add_argument(
        "--database-url",
        default=os.environ.get("RAG_EVAL_DATABASE_URL"),
        help=(
            "isolated local PostgreSQL *_test URL; defaults to "
            "RAG_EVAL_DATABASE_URL when set"
        ),
    )
    database_group.add_argument(
        "--use-default-test-database",
        action="store_true",
        help=(
            "derive the local *_test URL from DATABASE_URL; this clears only "
            "that explicit test database"
        ),
    )
    parser.add_argument("--limit", type=_positive_limit, default=5)
    parser.add_argument("--corpus", type=Path, default=data_dir / "corpus.json")
    parser.add_argument("--cases", type=Path, default=data_dir / "cases.json")
    parser.add_argument("--holdout", type=Path, default=data_dir / "holdout_cases.json")
    parser.add_argument("--thresholds", type=Path, default=data_dir / "thresholds.json")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "artifacts" / "evals" / "rag",
    )
    parser.add_argument(
        "--skip-rollback-drill",
        action="store_true",
        help="skip the publish-detect-withdraw-regression drill (not for release use)",
    )
    return parser


def _gateway(provider: str) -> CitationAnswerGateway:
    if provider == "fake":
        return FakeLLMGateway()
    from app.modules.llm_gateway.factory import get_gateway

    gateway = get_gateway()
    if gateway.provider_name == "fake":
        raise RagEvalConfigurationError(
            "--provider configured resolved to Fake; configure a non-fake "
            "LLM_PROVIDER and LLM_API_KEY"
        )
    return gateway


def _default_test_database_url() -> str:
    url = make_url(get_settings().database_url)
    database = url.database
    if not database:
        raise RagEvalConfigurationError("DATABASE_URL must include a database name")
    base = database.removesuffix("_test")
    # ``str(URL)`` intentionally masks passwords. This value stays inside the
    # process and is never logged, so render the usable URL only here.
    return url.set(database=f"{base}_test").render_as_string(hide_password=False)


def _validate_database_url(raw_url: str) -> URL:
    try:
        url = make_url(raw_url)
    except Exception as exc:
        raise RagEvalConfigurationError("invalid RAG evaluation database URL") from exc
    database = url.database or ""
    host = url.host or ""
    if not database.endswith("_test"):
        raise RagEvalConfigurationError(
            "RAG evaluation may only clear a database whose name ends in _test"
        )
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RagEvalConfigurationError(
            "RAG evaluation database must use a loopback PostgreSQL host"
        )
    if get_settings().app_env not in {"development", "test"}:
        raise RagEvalConfigurationError(
            "RAG evaluation database reset is allowed only in development/test"
        )
    return url


def _prepare_evaluation_database(
    raw_url: str,
) -> tuple[Engine, sessionmaker[Session]]:
    url = _validate_database_url(raw_url)
    api_root = Path(__file__).resolve().parents[2]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(config, "head")
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        seed(db)
    return engine, factory


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine: Engine | None = None
    try:
        raw_url = (
            _default_test_database_url()
            if args.use_default_test_database
            else args.database_url
        )
        if not raw_url:
            raise RagEvalConfigurationError(
                "provide --database-url/RAG_EVAL_DATABASE_URL or explicitly use "
                "--use-default-test-database"
            )
        corpus = load_corpus(args.corpus)
        cases = load_cases([args.cases, args.holdout])
        thresholds = load_thresholds(args.thresholds)
        gateway = _gateway(args.provider)
        if args.provider == "configured" and not args.expected_model:
            raise RagEvalConfigurationError(
                "--provider configured requires --expected-model to pin the "
                "evaluation model"
            )
        if args.expected_model and gateway.model_name != args.expected_model:
            raise RagEvalConfigurationError(
                "configured evaluation model mismatch: "
                f"expected={args.expected_model}, effective={gateway.model_name}"
            )
        if thresholds.provider != gateway.provider_name:
            raise RagEvalConfigurationError(
                "RAG threshold provider mismatch: "
                f"profile={thresholds.provider}, effective={gateway.provider_name}"
            )
        engine, factory = _prepare_evaluation_database(raw_url)
        with factory() as db:
            report = run_evaluation(
                db,
                corpus,
                cases,
                gateway=gateway,
                limit=args.limit,
                run_rollback_drill=not args.skip_rollback_drill,
            )
        threshold_results = apply_thresholds(report, thresholds)
        json_path, markdown_path = write_reports(report, args.output_dir)
    except RagEvalConfigurationError as exc:
        print(f"RAG evaluation configuration error: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        # Do not echo connection URLs, credentials, SQL, or corpus text from a
        # failed local database setup into CI logs or shell transcripts.
        print(
            "RAG evaluation database error: "
            f"{type(exc).__name__}; verify the isolated local *_test database",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()

    passed = all(item["passed"] for item in threshold_results)
    drill_passed = report.get("rollback_drill", {}).get("passed", True)
    passed = bool(passed and drill_passed)
    print(f"RAG evaluation: {'PASS' if passed else 'FAIL'}")
    print(
        "effective provider/model: "
        f"{report['versions']['provider']}/{report['versions']['model']}"
    )
    print(f"json report: {json_path}")
    print(f"markdown report: {markdown_path}")
    if not drill_passed:
        print("failed: error-policy withdrawal drill", file=sys.stderr)
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
