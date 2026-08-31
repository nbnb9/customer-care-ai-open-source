"""Versioned regression-case catalog for the supervisor QA workflow.

The catalog keeps a QA record from claiming a regression without a concrete
case that is actually loaded by one of the existing evaluators. It is a local
synthetic release artifact, not a substitute for a production CI attestation.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.qa import QAIssueCategory, QARemediationType


class QARegressionCatalogError(ValueError):
    """Raised when a checked-in QA regression catalog is not usable."""


class QARegressionCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    evaluation_suite: Literal["triage", "rag"]
    evaluation_case_id: str
    category: QAIssueCategory
    remediation_type: QARemediationType
    description: str


class QARegressionCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["qa-regression-catalog-v1"]
    source: Literal["synthetic"]
    cases: list[QARegressionCatalogEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> QARegressionCatalog:
        ids = [item.id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("QA regression catalog case IDs must be unique")
        targets = [
            (item.evaluation_suite, item.evaluation_case_id) for item in self.cases
        ]
        if len(targets) != len(set(targets)):
            raise ValueError("QA regression catalog evaluator targets must be unique")
        return self


def _repo_root() -> Path:
    # catalog.py lives at apps/api/app/modules/qa/, one level deeper than evals.
    return Path(__file__).resolve().parents[5]


def _catalog_path() -> Path:
    return _repo_root() / "data" / "evals" / "qa" / "regression-catalog.json"


def _evaluator_case_path(suite: Literal["triage", "rag"]) -> Path:
    return _repo_root() / "data" / "evals" / suite / "cases.json"


def _load_evaluator_case_ids(suite: Literal["triage", "rag"]) -> set[str]:
    try:
        raw = json.loads(_evaluator_case_path(suite).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QARegressionCatalogError(
            f"unable to load {suite} evaluator cases"
        ) from exc
    if not isinstance(raw, list) or not all(
        isinstance(item, dict) and isinstance(item.get("id"), str) for item in raw
    ):
        raise QARegressionCatalogError(f"{suite} evaluator cases have invalid shape")
    return {item["id"] for item in raw}


@lru_cache
def load_regression_catalog() -> QARegressionCatalog:
    try:
        raw = json.loads(_catalog_path().read_text(encoding="utf-8"))
        catalog = QARegressionCatalog.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise QARegressionCatalogError("invalid QA regression catalog") from exc

    ids_by_suite = {
        "triage": _load_evaluator_case_ids("triage"),
        "rag": _load_evaluator_case_ids("rag"),
    }
    missing = [
        f"{entry.evaluation_suite}:{entry.evaluation_case_id}"
        for entry in catalog.cases
        if entry.evaluation_case_id not in ids_by_suite[entry.evaluation_suite]
    ]
    if missing:
        raise QARegressionCatalogError(
            "QA regression catalog references missing evaluator cases: "
            + ", ".join(sorted(missing))
        )
    return catalog


def get_regression_case(case_id: str) -> QARegressionCatalogEntry:
    for entry in load_regression_catalog().cases:
        if entry.id == case_id:
            return entry
    raise QARegressionCatalogError("unknown QA regression case")
