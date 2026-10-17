"""Fish Audio 호출 — 목소리 등록(보이스 클로닝 모델 생성)·상태 조회·삭제.

녹음본을 POST /model 로 올려 Fish Audio 에 비공개 목소리 모델을 만든다. 돌려받은
모델 ID(_id)가 우리 speaker_id 가 되고, 합성할 때 /v1/tts 의 reference_id 로 쓴다.
train_mode=fast 는 보통 응답에서 바로 trained 로 오지만 보장되지는 않아서, 그렇지
않으면 상태 조회 때 GET /model/{id} 로 다시 확인한다.
문서: https://docs.fish.audio/api-reference/endpoint/model/create-model
"""

import logging

import httpx

from ..config import settings

logger = logging.getLogger("remory.fish_audio")

BASE_URL = "https://api.fish.audio"


class FishAudioError(Exception):
    """Fish Audio 호출 실패(연결 불가·타임아웃·비정상 응답)."""


def is_configured() -> bool:
    """FISH_API_KEY 가 있으면 등록을 시도한다.

    비어 있으면 등록을 건너뛴다(녹음만 받고 status=training 유지 — 데모/미연동).
    """
    return bool(settings.fish_api_key)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.fish_api_key}"}


async def create_model(title: str, audio: bytes, filename: str) -> tuple[str, str]:
    """녹음본으로 비공개 목소리 모델을 만들고 (모델 ID, state) 를 돌려준다.

    state 는 created | training | trained | failed. 실패하면 FishAudioError 를 던진다.
    """
    if not is_configured():
        raise FishAudioError("Fish Audio API 키(FISH_API_KEY)가 설정되지 않았습니다.")

    try:
        async with httpx.AsyncClient(timeout=settings.fish_timeout_sec) as client:
            resp = await client.post(
                f"{BASE_URL}/model",
                headers=_headers(),
                # 가족 목소리이므로 반드시 private. 대본(texts)은 안 보내 Fish 가 ASR 로 채운다.
                data={
                    "type": "tts",
                    "title": title,
                    "train_mode": "fast",
                    "visibility": "private",
                },
                files={"voices": (filename, audio)},
            )
    except httpx.HTTPError as e:
        logger.error("Fish Audio /model 호출 실패 title=%s: %s", title, e)
        raise FishAudioError(f"등록 서버 연결 실패: {e}") from e

    if resp.status_code not in (200, 201):
        logger.error(
            "Fish Audio /model 비정상 응답 title=%s status=%s body=%s",
            title,
            resp.status_code,
            resp.text[:300],
        )
        raise FishAudioError(f"등록 서버가 거부했습니다(status={resp.status_code}).")

    try:
        body = resp.json()
        return body["_id"], body.get("state") or "created"
    except (ValueError, KeyError) as e:
        raise FishAudioError("등록 서버 응답에 모델 ID가 없습니다.") from e


async def get_state(model_id: str) -> str:
    """모델의 현재 state 를 다시 확인한다. 실패하면 FishAudioError."""
    try:
        async with httpx.AsyncClient(timeout=settings.fish_timeout_sec) as client:
            resp = await client.get(f"{BASE_URL}/model/{model_id}", headers=_headers())
    except httpx.HTTPError as e:
        raise FishAudioError(f"등록 서버 연결 실패: {e}") from e

    if resp.status_code != 200:
        raise FishAudioError(f"등록 서버가 거부했습니다(status={resp.status_code}).")
    try:
        return resp.json().get("state") or "created"
    except ValueError as e:
        raise FishAudioError("등록 서버 응답을 읽지 못했습니다.") from e


async def delete_model(model_id: str) -> None:
    """Fish Audio 의 모델을 지운다. 실패해도 우리 쪽 삭제는 막지 않도록 로그만 남긴다."""
    if not is_configured():
        return
    try:
        async with httpx.AsyncClient(timeout=settings.fish_timeout_sec) as client:
            resp = await client.delete(f"{BASE_URL}/model/{model_id}", headers=_headers())
    except httpx.HTTPError as e:
        logger.error("Fish Audio 모델 삭제 실패 model_id=%s: %s", model_id, e)
        return
    if resp.status_code not in (200, 204, 404):
        logger.error(
            "Fish Audio 모델 삭제 비정상 응답 model_id=%s status=%s body=%s",
            model_id,
            resp.status_code,
            resp.text[:300],
        )
