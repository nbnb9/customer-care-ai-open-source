import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.evals import rag_cli
from app.modules.llm_gateway.base import FakeLLMGateway
from app.shared.config import get_settings

pytestmark = pytest.mark.offline


class _DummyEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _DummyFactory:
    def __call__(self):
        return self

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def _successful_report(*, drill_passed: bool = True) -> dict:
    return {
        "versions": {"provider": "fake", "model": "fake-deterministic-v1"},
        "rollback_drill": {"passed": drill_passed},
    }


def test_rag_cli_defaults_to_fake_and_requires_explicit_test_database(
    monkeypatch, capsys
):
    monkeypatch.delenv("RAG_EVAL_DATABASE_URL", raising=False)
    parser = rag_cli.build_parser()
    args = parser.parse_args([])

    assert args.provider == "fake"
    assert args.limit == 5
    assert args.corpus.name == "corpus.json"
    assert args.holdout.name == "holdout_cases.json"
    assert rag_cli.main([]) == 2
    assert "provide --database-url" in capsys.readouterr().err


def test_configured_rag_cli_requires_an_explicit_model_pin(monkeypatch, capsys):
    class FixedGateway(FakeLLMGateway):
        provider_name = "fixed-provider"
        model_name = "fixed-model-2026-08"

    monkeypatch.setattr(rag_cli, "_gateway", lambda provider: FixedGateway())
    monkeypatch.setattr(
        rag_cli,
        "_prepare_evaluation_database",
        lambda raw_url: (_ for _ in ()).throw(AssertionError("must not open database")),
    )

    assert (
        rag_cli.main(
            [
                "--provider",
                "configured",
                "--database-url",
                "postgresql+psycopg://localhost/customer_care_test",
            ]
        )
        == 2
    )
    assert "requires --expected-model" in capsys.readouterr().err

    assert (
        rag_cli.main(
            [
                "--provider",
                "configured",
                "--expected-model",
                "different-model",
                "--database-url",
                "postgresql+psycopg://localhost/customer_care_test",
            ]
        )
        == 2
    )
    assert "model mismatch" in capsys.readouterr().err


def test_rag_cli_rejects_non_test_database_before_any_reset(capsys):
    assert (
        rag_cli.main(["--database-url", "postgresql+psycopg://localhost/customer_care"])
        == 2
    )
    assert "name ends in _test" in capsys.readouterr().err


def test_default_test_database_url_preserves_connection_credentials():
    configured = make_url(get_settings().database_url)
    derived = make_url(rag_cli._default_test_database_url())

    assert configured.database is not None
    assert derived.database == f"{configured.database.removesuffix('_test')}_test"
    assert derived.password == configured.password


def test_rag_cli_runs_fixed_fake_gate_and_disposes_database_engine(
    monkeypatch, tmp_path, capsys
):
    engine = _DummyEngine()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        rag_cli,
        "_prepare_evaluation_database",
        lambda raw_url: (engine, _DummyFactory()),
    )
    monkeypatch.setattr(
        rag_cli,
        "run_evaluation",
        lambda db, corpus, cases, **kwargs: (
            captured.update(kwargs) or _successful_report()
        ),
    )
    monkeypatch.setattr(
        rag_cli,
        "apply_thresholds",
        lambda report, thresholds: [{"passed": True}],
    )
    monkeypatch.setattr(
        rag_cli,
        "write_reports",
        lambda report, output_dir: (tmp_path / "report.json", tmp_path / "report.md"),
    )

    assert (
        rag_cli.main(
            ["--database-url", "postgresql+psycopg://localhost/customer_care_test"]
        )
        == 0
    )
    assert captured["run_rollback_drill"] is True
    assert engine.disposed is True
    output = capsys.readouterr().out
    assert "RAG evaluation: PASS" in output
    assert "fake/fake-deterministic-v1" in output


def test_rag_cli_fails_threshold_or_drill_without_hiding_failed_metric(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        rag_cli,
        "_prepare_evaluation_database",
        lambda raw_url: (_DummyEngine(), _DummyFactory()),
    )
    monkeypatch.setattr(
        rag_cli,
        "run_evaluation",
        lambda db, corpus, cases, **kwargs: _successful_report(drill_passed=False),
    )
    monkeypatch.setattr(
        rag_cli,
        "apply_thresholds",
        lambda report, thresholds: [
            {
                "passed": False,
                "metric": "recall_at_k",
                "actual": 0.0,
                "comparator": "min",
                "target": 1.0,
            }
        ],
    )
    monkeypatch.setattr(
        rag_cli,
        "write_reports",
        lambda report, output_dir: (tmp_path / "report.json", tmp_path / "report.md"),
    )

    assert (
        rag_cli.main(
            [
                "--database-url",
                "postgresql+psycopg://localhost/customer_care_test",
                "--skip-rollback-drill",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "RAG evaluation: FAIL" in captured.out
    assert "failed: error-policy withdrawal drill" in captured.err
    assert "failed: recall_at_k actual=0.0 min=1.0" in captured.err


def test_rag_cli_redacts_database_exception_details(monkeypatch, capsys):
    monkeypatch.setattr(
        rag_cli,
        "_prepare_evaluation_database",
        lambda raw_url: (_ for _ in ()).throw(
            OperationalError("sensitive database detail", None, None)
        ),
    )

    assert (
        rag_cli.main(
            ["--database-url", "postgresql+psycopg://localhost/customer_care_test"]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "OperationalError" in error
    assert "sensitive database detail" not in error


def test_cli_helpers_validate_limits_hosts_and_configured_gateway(monkeypatch):
    assert rag_cli._positive_limit("1") == 1
    with pytest.raises(Exception, match="between 1 and 20"):
        rag_cli._positive_limit("21")
    with pytest.raises(Exception, match="loopback"):
        rag_cli._validate_database_url(
            "postgresql+psycopg://example.test/customer_care_test"
        )
    assert rag_cli._gateway("fake").provider_name == "fake"

    from app.modules.llm_gateway import factory

    monkeypatch.setattr(factory, "get_gateway", lambda: FakeLLMGateway())
    with pytest.raises(Exception, match="resolved to Fake"):
        rag_cli._gateway("configured")
