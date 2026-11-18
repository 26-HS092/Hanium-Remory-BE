"""리포트 문구를 LLM 으로 만든다. 데일리와 주간 둘 다 여기서 쓴다.

LLM_MODEL 이 claude 로 시작하면 Claude(ANTHROPIC_API_KEY), 아니면 Groq
(GROQ_API_KEY) 를 부른다. LLM_MODEL=claude 로만 적으면 CLAUDE_DEFAULT_MODEL 을 쓴다.

숫자를 문장으로 바꾸는 일은 규칙으로 짜면 금방 어색해진다(가족과 0번,
대화만 1번 같은 조합이 계속 늘어난다). 요약과 제안 두 문장만 모델에게 맡긴다.

키가 없거나 호출이 실패하면 None 을 준다. 부르는 쪽이 규칙 기반 문구로
물러나므로 배치가 멈추지 않는다 — 리포트가 아예 안 만들어지는 것보다
문구가 덜 자연스러운 편이 낫다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from ..config import settings

logger = logging.getLogger("remory.llm")

# 한 번에 받아 올 최대 출력 토큰.
#
# 추론형 모델은 답을 내기 전에 토큰을 꽤 쓴다. 500 으로 뒀더니 JSON 을 다
# 못 만들고 잘려서 실패했다. 그래서 1500 으로 올렸는데, 이번엔 Groq 무료
# 등급의 분당 출력 한도(OTPM 1000)를 넘어 요청 자체가 429 로 막혔다
# ("Requested 1110 > Limit 1000"). 그러면 리포트 문구가 늘 규칙 기반으로
# 떨어지는데, 로그를 보지 않으면 알아채기 어렵다.
#
# 한도 아래이면서 JSON 을 다 만들 만큼은 되는 값으로 둔다. 등급을 올리면
# 이 값을 키워도 된다.
MAX_OUTPUT_TOKENS = 900

# LLM_MODEL=claude 로만 적었을 때 쓸 모델.
CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"
# Claude 는 답하기 전에 생각(thinking)을 하므로 출력 한도를 넉넉히 둔다.
# Groq 처럼 분당 출력 한도에 걸릴 일이 없어 900 으로 묶을 이유가 없다.
CLAUDE_MAX_OUTPUT_TOKENS = 16000

# 마지막으로 모델을 부른 시각. 잇따라 부르지 않으려고 든다.
_last_call_at = 0.0
_call_lock = threading.Lock()


def _wait_turn() -> None:
    """지난번 호출로부터 llm_min_gap_sec 이 지날 때까지 기다린다.

    무료 등급은 분당 출력 토큰이 정해져 있어 잇따라 부르면 뒤엣것이 429 로
    막힌다. 어르신이 여럿이면 첫 사람 것만 모델이 쓰고 나머지는 규칙 기반으로
    떨어지는데, 로그를 보지 않으면 알아채기 어렵다. 리포트는 밤에 도는
    배치라 빨리 끝날 이유가 없으므로 쉬어 가며 부른다.
    """
    gap = settings.llm_min_gap_sec
    if gap <= 0:
        return
    with _call_lock:
        global _last_call_at
        remain = gap - (time.monotonic() - _last_call_at)
        if remain > 0:
            logger.info("한도에 걸리지 않게 %.0f초 쉰다.", remain)
            time.sleep(remain)
        _last_call_at = time.monotonic()


def _is_rate_limited(error: Exception) -> bool:
    text = str(error).lower()
    return "rate_limit" in text or "429" in text or "too large" in text


def _use_claude() -> bool:
    return settings.llm_model.strip().lower().startswith("claude")


def _claude_model() -> str:
    """LLM_MODEL=claude 면 기본 모델, claude-sonnet-5-5 처럼 적었으면 그 모델."""
    name = settings.llm_model.strip()
    return CLAUDE_DEFAULT_MODEL if name.lower() == "claude" else name


def _configured() -> bool:
    """고른 모델에 맞는 키가 있는지. 없으면 모델을 부르지 않고 규칙 기반 문구로 간다."""
    return bool(settings.anthropic_api_key if _use_claude() else settings.groq_api_key)


def _ask(system: str, user: str, schema, temperature: float):
    """모델에게 한 번 묻고 [schema] 로 받아 온다. 못 받으면 None.

    부르는 쪽은 모두 규칙 기반 문구로 물러날 수 있으므로, 여기서 나는 문제는
    로그만 남기고 None 을 준다. 배치가 멈추지 않는 편이 낫다.
    """
    if _use_claude():
        return _ask_claude(system, user, schema)
    return _ask_groq(system, user, schema, temperature)


# 구조화 출력(json_schema)이 받지 않는 제약. 길이·개수 제한은 받아 온 뒤
# pydantic 이 다시 검사하므로 스키마에서는 빼도 된다.
_UNSUPPORTED_SCHEMA_KEYS = {"title", "default", "minLength", "maxLength", "minItems", "maxItems"}


def _output_schema(node):
    """pydantic 스키마를 구조화 출력용으로 다듬는다. 객체는 모든 필드를 필수로, 추가 필드는 막는다."""
    if isinstance(node, list):
        return [_output_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {
        k: _output_schema(v)
        for k, v in node.items()
        if k not in _UNSUPPORTED_SCHEMA_KEYS
        or k in ("properties", "$defs")  # 필드 이름이 title 같은 경우를 지키려고
    }
    if out.get("type") == "object" and "properties" in out:
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


def _ask_claude(system: str, user: str, schema):
    """Claude 로 묻는다. 구조화 출력으로 [schema] 꼴의 JSON 만 받는다.

    temperature 는 넘기지 않는다(최신 API 버전은 받지 않는다). 안전 분류기가
    거절하면 서버가 다른 모델로 다시 해 보게 fallbacks 를 켠다.
    429·5xx·연결 오류는 SDK 가 알아서 다시 시도한다.
    """
    if not settings.anthropic_api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.beta.messages.create(
            model=_claude_model(),
            max_tokens=CLAUDE_MAX_OUTPUT_TOKENS,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _output_schema(schema.model_json_schema()),
                }
            },
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        logger.warning("Claude 호출 실패(%s: %s). 기본 문구를 쓴다.", type(e).__name__, e)
        return None

    if response.stop_reason != "end_turn":
        # refusal(대체 모델까지 거절) 이거나 max_tokens 로 잘린 경우.
        logger.warning("Claude 응답이 끝나지 않음(stop_reason=%s). 기본 문구를 쓴다.", response.stop_reason)
        return None

    raw = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return schema.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("응답 형식이 어긋남(%s). 기본 문구를 쓴다.", e)
        return None


def _ask_groq(system: str, user: str, schema, temperature: float):
    """Groq 로 묻는다. 무료 등급 한도에 걸리지 않게 쉬어 가며 부른다."""
    if not settings.groq_api_key:
        return None

    try:
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key)
    except Exception as e:
        logger.warning("Groq 준비 실패(%s: %s).", type(e).__name__, e)
        return None

    for attempt in (1, 2):
        _wait_turn()
        try:
            response = client.chat.completions.create(
                model=settings.llm_model,
                response_format={"type": "json_object"},
                temperature=temperature,
                max_completion_tokens=MAX_OUTPUT_TOKENS,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            raw = response.choices[0].message.content or ""
            return schema.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            # JSON 모드라도 형태가 어긋날 수 있다. 다시 물어도 같을 가능성이 커서
            # 한 번만 하고 물러난다.
            logger.warning("응답 형식이 어긋남(%s). 기본 문구를 쓴다.", e)
            return None
        except Exception as e:
            if attempt == 1 and _is_rate_limited(e):
                logger.info("한도에 걸렸다. 쉬었다가 한 번 더 해 본다.")
                continue                      # _wait_turn 이 다시 쉬어 준다
            logger.warning("호출 실패(%s: %s). 기본 문구를 쓴다.", type(e).__name__, e)
            return None
    return None

SYSTEM = """너는 치매 어르신을 돌보는 가족에게 하루 요약을 전하는 도우미다.

지켜야 할 것:
- 존댓말로, 따뜻하지만 담담하게 쓴다. 과장하거나 걱정을 부추기지 않는다.
- 주어진 것 말고 다른 사실을 지어내지 않는다. 대화 내용이 주어지지 않은 날은
  무슨 이야기를 했는지 알 수 없으므로 쓰지 않는다.
- '짚어야 할 일' 이 주어지면 반드시 요약에 담되, 진단하듯 단정하지 말고
  가족이 오늘 살펴볼 만한 일로 담담히 적는다.
- 대화 내용이 주어지면 거기서 드러난 것만 쓴다. 말 그대로 옮기지 말고 한 마디로
  간추린다. 병·증상을 진단하듯 단정하지 않는다.
- 기록이 적은 날은 적은 대로 담백하게 쓴다. 억지로 좋게 포장하지 않는다.
- 각 문장은 한국어로 두 문장을 넘기지 않는다.
- 성함은 주어진 그대로 쓴다. 성을 빼거나 줄이지 않는다.

반드시 아래 형태의 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
{"summary": "오늘 하루 요약", "suggestion": "보호자가 오늘 해볼 만한 것"}"""


class ReportText(BaseModel):
    """모델이 채워 줄 두 문장."""

    summary: str = Field(min_length=1, max_length=400)
    suggestion: str = Field(min_length=1, max_length=400)


def _prompt(
    name: str,
    conversations: int,
    family: int,
    emotion_label: Optional[str],
    transcript: Optional[str],
    safety_note: Optional[str] = None,
) -> str:
    body = (
        f"어르신 성함: {name}\n"
        f"인형과 나눈 대화: {conversations}번\n"
        f"가족이 대화방에 남긴 메시지: {family}번\n"
        f"오늘 가장 많이 기록된 감정: {emotion_label or '기록 없음'}\n"
    )
    if transcript:
        body += f"\n오늘 나눈 대화:\n{transcript}\n"
    if safety_note:
        body += f"\n짚어야 할 일: {safety_note}\n"
    return body + "\n위 기록만 가지고 요약과 제안을 JSON 으로 써 줘."


def write_report_text(
    name: str,
    conversations: int,
    family: int,
    emotion_label: Optional[str],
    transcript: Optional[str] = None,
    safety_note: Optional[str] = None,
) -> Optional[ReportText]:
    """요약·제안 문구를 만든다. 못 만들면 None.

    [transcript] 는 그날 인형과 나눈 대화를 "어르신: ...", "모리: ..." 로
    붙여 놓은 글이다. 있으면 숫자만 세는 대신 무슨 이야기를 했는지까지
    반영한다. 없으면 예전처럼 숫자만 가지고 쓴다.
    """
    return _ask(
        SYSTEM,
        _prompt(name, conversations, family, emotion_label, transcript, safety_note),
        ReportText,
        temperature=0.4,
    )


WEEKLY_SYSTEM = """너는 치매 어르신을 돌보는 가족에게 한 주 요약을 전하는 도우미다.

지켜야 할 것:
- 존댓말로, 따뜻하지만 담담하게 쓴다. 과장하거나 걱정을 부추기지 않는다.
- 주어진 숫자 말고 다른 사실을 지어내지 않는다.
- 한 주 동안의 흐름(늘었는지 줄었는지, 어떤 감정이 잦았는지)을 짚어 준다.
- 기록이 적은 주는 적은 대로 담백하게 쓴다. 억지로 좋게 포장하지 않는다.
- 한국어로 세 문장을 넘기지 않는다.
- 성함은 주어진 그대로 쓴다. 성을 빼거나 줄이지 않는다.

반드시 아래 형태의 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
{"summary": "한 주 요약"}"""


class WeeklyText(BaseModel):
    """모델이 채워 줄 한 주 요약."""

    summary: str = Field(min_length=1, max_length=600)


def _weekly_prompt(
    name: str,
    conversations: int,
    family: int,
    emotion_label: Optional[str],
    urgent: int,
    daily_lines: str,
) -> str:
    return (
        f"어르신 성함: {name}\n"
        f"이번 주 인형과 나눈 대화: {conversations}번\n"
        f"이번 주 가족이 남긴 메시지: {family}번\n"
        f"이번 주 가장 많이 기록된 감정: {emotion_label or '기록 없음'}\n"
        f"이번 주 긴급 알림: {urgent}번\n\n"
        f"날짜별 기록:\n{daily_lines or '기록 없음'}\n\n"
        "위 기록만 가지고 한 주 요약을 JSON 으로 써 줘."
    )


def write_weekly_text(
    name: str,
    conversations: int,
    family: int,
    emotion_label: Optional[str],
    urgent: int,
    daily_lines: str,
) -> Optional[WeeklyText]:
    """주간 요약 문구를 만든다. 못 만들면 None (부르는 쪽이 기본 문구로 간다)."""
    return _ask(
        WEEKLY_SYSTEM,
        _weekly_prompt(name, conversations, family, emotion_label, urgent, daily_lines),
        WeeklyText,
        temperature=0.4,
    )


EXCERPT_SYSTEM = """너는 치매 어르신이 인형과 나눈 하루치 대화를 읽고, 가족에게
보여줄 대목을 골라 주는 도우미다.

번호가 붙은 대화가 주어진다. 그중 가족이 보면 좋을 대목을 최대 3개 고르고,
고른 번호와 함께 그 말을 다듬어 돌려준다.

고르는 기준:
- 그날 어르신이 어떻게 지내셨는지 드러나는 대목. 몸 상태, 가족 이야기, 드신
  것, 기분이 드러난 말.
- "응", "그래" 같은 맞장구만 있는 대목은 고르지 않는다.
- 뜻을 알아볼 수 없는 대목은 고르지 않는다.

다듬는 규칙:
- 이 글은 받아쓴 것이라 잘못 적힌 말이 섞여 있다. 앞뒤로 보아 분명한 오타는
  바로잡는다(예: "무릅" → "무릎").
- 그 밖에는 하신 말을 그대로 둔다. 말투와 어미를 바꾸지 말고, 요약하지도
  말고, 없는 말을 보태지도 마라.
- 너무 길면 뒤를 자르되 뜻이 끊기지 않는 데까지만 둔다.
- 고칠 것이 없으면 원문 그대로 돌려준다.

고를 만한 대목이 하나도 없으면 빈 배열을 준다.

반드시 아래 형태의 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
{"picks": [{"no": 1, "user": "어르신이 하신 말", "mori": "모리가 한 답"}]}"""


class ExcerptPick(BaseModel):
    """고른 대목 하나."""

    no: int
    user: str = Field(min_length=1, max_length=200)
    mori: str = Field(default="", max_length=200)


class ExcerptPicks(BaseModel):
    picks: list[ExcerptPick] = Field(max_length=6)


def write_conversation_excerpt(name: str, numbered: str) -> Optional[list[dict]]:
    """보여줄 대목을 고르고 다듬어 준다. 못 만들면 None.

    [numbered] 는 "1. 어르신: ... / 모리: ..." 처럼 번호를 붙인 그날 대화다.
    시각은 모델에게 맡기지 않는다 — 번호만 고르게 하고, 부르는 쪽이 그 번호로
    실제 시각을 되찾아 붙인다. 모델이 시각을 지어낼 자리를 아예 없앤다.
    """
    if not _configured() or not numbered:
        return None
    parsed = _ask(
        EXCERPT_SYSTEM,
        f"어르신 성함: {name}\n\n오늘 나눈 대화:\n{numbered}",
        ExcerptPicks,
        temperature=0.2,
    )
    if parsed is None:
        return None
    return [p.model_dump() for p in parsed.picks] or None


STORY_SYSTEM = """너는 치매 어르신을 돌보는 가족에게 오늘 하루가 어땠는지
들려주는 도우미다.

머리말이 하나 주어진다. 화면 맨 위에 이미 크게 걸려 있는 글이므로 같은 말을
되풀이하지 마라. 너는 그 아래에서 하루를 이어서 들려준다.

지켜야 할 것:
- 존댓말로, 따뜻하지만 담담하게 쓴다. 과장하거나 걱정을 부추기지 않는다.
- 주어진 기록에 있는 것만 쓴다. 없는 일을 지어내지 않는다. 숫자를 다시
  늘어놓지 말고, 어떤 하루였는지가 드러나게 쓴다.
- 세 문장에서 다섯 문장 사이로 쓴다. 한 문단이면 된다.
- 아침·낮·저녁처럼 시간이 드러나면 그 흐름을 따라 쓴다.
- 받아쓴 글이라 잘못 적힌 말이 섞여 있다. 분명하지 않은 대목은 넘긴다.
- 진단하듯 단정하지 않는다("치매가 악화되었습니다" 같은 말은 쓰지 않는다).
- 기록이 적은 날은 적은 대로 짧게 쓴다. 억지로 채우지 않는다.

반드시 아래 형태의 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
{"story": "하루 이야기"}"""


class DayStory(BaseModel):
    """하루를 풀어 쓴 한 문단."""

    story: str = Field(min_length=1, max_length=800)


def write_day_story(
    name: str,
    headline: Optional[str],
    transcript: Optional[str],
    emotion_label: Optional[str],
    activities: Optional[str] = None,
    safety_note: Optional[str] = None,
) -> Optional[str]:
    """오늘 하루가 어땠는지 한 문단으로. 못 만들면 None.

    [headline] 은 화면 맨 위에 걸리는 요약이다. 같은 말을 되풀이하지 않도록
    모델에게 함께 준다. 요약과 따로 부르는 이유는, 한 번에 다 받으면 출력이
    길어져 JSON 이 잘리는 일이 생기기 때문이다 — 그러면 요약까지 함께 잃는다.
    """
    if not transcript and not activities:
        return None

    body = f"어르신 성함: {name}\n"
    if headline:
        body += f"이미 걸려 있는 머리말: {headline}\n"
    if emotion_label:
        body += f"오늘 가장 많이 기록된 감정: {emotion_label}\n"
    if activities:
        body += f"\n오늘의 일과:\n{activities}\n"
    if transcript:
        body += f"\n오늘 나눈 대화:\n{transcript}\n"
    if safety_note:
        body += f"\n짚어야 할 일: {safety_note}\n"
    body += "\n위 기록만 가지고 하루 이야기를 JSON 으로 써 줘."

    parsed = _ask(STORY_SYSTEM, body, DayStory, temperature=0.4)
    return parsed.story.strip() if parsed else None


WEEK_STORY_SYSTEM = """너는 치매 어르신을 돌보는 가족에게 한 주가 어땠는지
돌아봐 주는 도우미다.

머리말이 하나 주어진다. 화면 맨 위에 이미 걸려 있는 글이므로 같은 말을
되풀이하지 마라. 너는 그 아래에서 한 주를 이어서 들려준다.

지켜야 할 것:
- 존댓말로, 따뜻하지만 담담하게 쓴다. 과장하거나 걱정을 부추기지 않는다.
- 날짜별 기록에 있는 것만 쓴다. 없는 일을 지어내지 않는다.
- 한 주의 흐름을 짚는다. 어느 요일이 어땠는지, 나아졌는지 처졌는지처럼
  하루치만 봐서는 알 수 없는 것을 쓴다.
- 세 문장에서 다섯 문장 사이로 쓴다. 한 문단이면 된다.
- 마지막 한 문장은 다음 주에 가족이 해볼 만한 것으로 맺는다.
- 진단하듯 단정하지 않는다.
- 기록이 적은 주는 적은 대로 짧게 쓴다. 억지로 채우지 않는다.

반드시 아래 형태의 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
{"story": "한 주 이야기"}"""


def write_week_story(
    name: str,
    headline: Optional[str],
    daily_lines: str,
    emotion_label: Optional[str],
    urgent: int,
) -> Optional[str]:
    """한 주가 어땠는지 한 문단으로. 못 만들면 None.

    [daily_lines] 는 날짜별 대화·가족·감정을 한 줄씩 적어 놓은 글이다.
    하루치만 봐서는 알 수 없는 흐름을 여기서 짚는다.
    """
    if not daily_lines:
        return None

    body = f"어르신 성함: {name}\n"
    if headline:
        body += f"이미 걸려 있는 머리말: {headline}\n"
    if emotion_label:
        body += f"이번 주 가장 많이 기록된 감정: {emotion_label}\n"
    body += f"이번 주 긴급 알림: {urgent}번\n"
    body += f"\n날짜별 기록:\n{daily_lines}\n"
    body += "\n위 기록만 가지고 한 주 이야기를 JSON 으로 써 줘."

    parsed = _ask(WEEK_STORY_SYSTEM, body, DayStory, temperature=0.4)
    return parsed.story.strip() if parsed else None


KEYWORDS_SYSTEM = """너는 치매 어르신이 한 주 동안 인형과 나눈 대화를 읽고,
자주 나온 이야깃거리를 골라 주는 도우미다.

번호가 붙은 대화가 주어진다. 그중 되풀이해서 나온 이야깃거리를 최대 5개
고르고, 각각이 몇 번 대화에 나왔는지 번호를 모아 준다.

지켜야 할 것:
- 이야깃거리는 두세 글자 명사로 쓴다. 예: "손주", "식사", "날씨", "옛 추억", "교회"
- 한 번만 나온 것은 고르지 않는다. 자주 나온 것이 무엇인지 보여주는 자리다.
- 번호는 실제로 그 이야기가 나온 대화의 번호만 적는다. 세지 말고 번호만 모아라.
  세는 일은 다른 데서 한다.
- "이야기", "생각", "오늘" 처럼 아무 데나 붙는 말은 고르지 않는다.
- 뜻을 알아볼 수 없는 대목은 넘긴다. 받아쓴 글이라 잘못 적힌 말이 섞여 있다.

고를 만한 것이 없으면 빈 배열을 준다.

반드시 아래 형태의 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
{"keywords": [{"word": "손주", "turns": [1, 4, 9]}]}"""


class KeywordPick(BaseModel):
    word: str = Field(min_length=1, max_length=20)
    turns: list[int] = Field(default_factory=list)


class KeywordPicks(BaseModel):
    keywords: list[KeywordPick] = Field(max_length=10)


def write_week_keywords(name: str, numbered: str) -> Optional[list[dict]]:
    """자주 나온 이야깃거리와 그것이 나온 대화 번호. 못 만들면 None.

    횟수는 모델에게 맡기지 않는다. 번호만 모으게 하고 세는 일은 부르는 쪽이
    한다 — 모델이 센 숫자를 '12번' 이라고 화면에 내걸 수는 없다. 발췌에서
    시각을 모델에게 맡기지 않은 것과 같은 이유다.
    """
    if not numbered:
        return None
    parsed = _ask(KEYWORDS_SYSTEM, f"어르신 성함: {name}\n\n한 주 동안 나눈 대화:\n{numbered}",
                  KeywordPicks, temperature=0.2)
    if parsed is None:
        return None
    return [k.model_dump() for k in parsed.keywords] or None
