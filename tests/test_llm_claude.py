"""LLM_MODEL=claude 면 Groq 대신 Claude 로 리포트 문구를 만드는지."""

import pytest
from pydantic import BaseModel, Field

from app.services import llm


class Answer(BaseModel):
    ok: bool


class Pick(BaseModel):
    no: int
    user: str = Field(min_length=1, max_length=200)
    mori: str = Field(default="", max_length=200)


class Picks(BaseModel):
    picks: list[Pick] = Field(max_length=6)


@pytest.fixture
def use_claude(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_model", "claude")
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "groq_api_key", "")


def _stub_claude(monkeypatch, text='{"ok": true}', stop_reason="end_turn"):
    """Claude 대신 미리 정한 답을 내놓는다. 넘어간 인자를 모아 둔다."""
    calls = []

    class Block:
        type = "text"

        def __init__(self, text): self.text = text

    class Response:
        def __init__(self):
            self.stop_reason = stop_reason
            self.content = [Block(text)]

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return Response()

    class Beta:
        messages = Messages()

    class FakeAnthropic:
        def __init__(self, api_key=None):
            calls.append({"api_key": api_key})
            self.beta = Beta()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return calls


def test_claude_is_used_with_its_own_key(use_claude, monkeypatch):
    calls = _stub_claude(monkeypatch)

    assert llm._ask("시스템", "질문", Answer, temperature=0.4).ok is True
    assert calls[0] == {"api_key": "test-key"}
    req = calls[1]
    assert req["model"] == llm.CLAUDE_DEFAULT_MODEL
    assert req["system"] == "시스템"
    assert req["messages"] == [{"role": "user", "content": "질문"}]
    assert "temperature" not in req, "최신 API 버전은 temperature 를 받지 않는다"
    assert req["fallbacks"] == "default"


def test_full_claude_model_id_is_used_as_is(use_claude, monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_model", "claude-sonnet-5-5")
    calls = _stub_claude(monkeypatch)

    llm._ask("s", "u", Answer, temperature=0.4)
    assert calls[1]["model"] == "claude-sonnet-5-5"


def test_no_key_skips_the_call(use_claude, monkeypatch):
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "")
    calls = _stub_claude(monkeypatch)

    assert llm._ask("s", "u", Answer, temperature=0.4) is None
    assert calls == []


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unfinished_answer_falls_back(use_claude, monkeypatch, stop_reason):
    _stub_claude(monkeypatch, stop_reason=stop_reason)
    assert llm._ask("s", "u", Answer, temperature=0.4) is None


def test_bad_json_falls_back(use_claude, monkeypatch):
    _stub_claude(monkeypatch, text="그냥 문장")
    assert llm._ask("s", "u", Answer, temperature=0.4) is None


def test_output_schema_drops_length_limits_and_closes_objects():
    """구조화 출력이 받지 않는 제약은 빼고, 객체는 모든 필드 필수·추가 필드 금지."""
    schema = llm._output_schema(Picks.model_json_schema())
    pick = schema["$defs"]["Pick"]

    assert pick["required"] == ["no", "user", "mori"]
    assert pick["additionalProperties"] is False
    assert "maxLength" not in pick["properties"]["user"]
    assert "default" not in pick["properties"]["mori"]
    assert "maxItems" not in schema["properties"]["picks"]
    assert schema["additionalProperties"] is False


def test_excerpt_runs_with_only_the_claude_key(use_claude, monkeypatch):
    """Groq 키가 없어도 Claude 키만 있으면 대화 발췌를 만든다."""
    _stub_claude(monkeypatch, text='{"picks": [{"no": 1, "user": "무릎이 아파", "mori": "쉬세요"}]}')
    got = llm.write_conversation_excerpt("박순자", "1. 어르신: 무릅이 아파 / 모리: 쉬세요")
    assert got == [{"no": 1, "user": "무릎이 아파", "mori": "쉬세요"}]
