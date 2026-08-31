import json
from pathlib import Path

from app.modules.llm_gateway.base import FakeLLMGateway, generate_triage_safe
from app.modules.llm_gateway.schemas import RiskLevel, Route, TriageResult


def _load_cases():
    # 测试文件在 apps/api/tests/，仓库根是 parents[3]
    path = (
        Path(__file__).resolve().parents[3] / "data" / "evals" / "triage" / "cases.json"
    )
    return json.loads(path.read_text())


def test_at_least_20_cases():
    assert len(_load_cases()) >= 20


def test_fake_gateway_returns_valid_schema():
    gw = FakeLLMGateway()
    for case in _load_cases():
        result = gw.generate_structured(text=case["text"])
        assert isinstance(result, TriageResult)  # Schema 合法


def test_fake_gateway_matches_complete_expected_output():
    gw = FakeLLMGateway()
    expected_priority = {"R0": "low", "R1": "normal", "R2": "high", "R3": "urgent"}
    for case in _load_cases():
        result = gw.generate_structured(text=case["text"])
        expected = case["expected"]
        assert result.intent.value == expected["intent"], case["id"]
        assert result.risk_level.value == expected["risk_level"], case["id"]
        assert result.route.value == expected["route"], case["id"]
        assert (
            result.suggested_priority.value == expected_priority[expected["risk_level"]]
        ), case["id"]
        assert result.needs_human_review is expected.get("needs_human_review", False), (
            case["id"]
        )
        assert result.missing_information == expected.get("missing_information", []), (
            case["id"]
        )


class BrokenGateway:
    """返回无效输出/抛校验异常的网关，用于测安全回退。"""

    def generate_structured(self, *, text: str):
        raise ValueError("invalid structured output")


def test_schema_failure_returns_safe_fallback():
    result = generate_triage_safe(BrokenGateway(), text="任意文本")
    assert result.summary == "无法生成建议，请人工处理"
    assert result.route is Route.human_handoff
    assert result.needs_human_review is True
    assert result.risk_level is RiskLevel.R3
    assert result.suggested_priority.value == "urgent"
