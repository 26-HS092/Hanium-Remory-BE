"""가족 목소리 등록(Fish Audio 보이스 클로닝)·상태 조회·삭제.

흐름: 가족이 녹음 업로드 → EC2 가 Fish Audio 에 비공개 목소리 모델 생성(fast, 보통 즉시)
      → status=ready(+speaker_id=모델 ID) → PATCH /devices/{id}/settings/voice 로 기본 음성 지정.
      생성 응답이 아직 trained 가 아니면 training 으로 두고, 상태 조회 때 Fish 에 다시 묻는다.
음성은 기기(device)에 묶이고, 누가 녹음했는지는 protector_id 로 남는다.
"""

import os

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_protector
from ..errors import APIError, envelope
from ..models import Protector, Voice
from ..services import fish_audio
from ..services.access import (
    ensure_default_voice,
    get_owned_device,
    voice_json,
    voice_owners,
)
from ..services.notifications import notify_voice_ready
from ..services.storage import storage

router = APIRouter(tags=["voices"])

VOICE_PREFIX = "voices"
# Fish Audio 가 받는 형식만 허용한다(wav·mp3·m4a·opus).
ALLOWED_EXT = {".wav", ".mp3", ".m4a", ".opus"}
MAX_BYTES = 30 * 1024 * 1024  # 30MB


def _apply_state(db: Session, voice: Voice, device, state: str) -> None:
    """Fish Audio 모델 state 를 우리 목소리 상태로 옮긴다. 막 ready 가 되면 등록한 본인에게 알린다."""
    if state == "trained":
        if voice.status != "ready":
            voice.status = "ready"
            voice.progress = 100
            db.commit()
            if device.user_id is not None and voice.protector_id is not None:
                notify_voice_ready(db, device.user_id, voice.protector_id, voice.name)
    elif state == "failed":
        voice.status = "failed"
        voice.error_message = "목소리 학습에 실패했습니다. 조용한 곳에서 다시 녹음해주세요."
        db.commit()


@router.post("/devices/{device_id}/voices", status_code=201)
async def register_voice(
    device_id: int,
    name: str = Form(..., description="음성 이름 (예: '딸 지영')"),
    file: UploadFile = File(..., description="녹음된 음성 파일"),
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """음성 녹음 등록 → Fish Audio 목소리 모델 생성."""
    device = get_owned_device(db, protector, device_id)

    if not file.filename:
        raise APIError(400, "파일 이름이 없습니다.")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise APIError(400, "음성 파일만 올릴 수 있습니다.")

    content = await file.read()
    if len(content) > MAX_BYTES:
        raise APIError(400, "30MB 이하의 음성만 올릴 수 있습니다.")

    audio_url = storage.save(content, ext, prefix=VOICE_PREFIX)

    voice = Voice(
        device_id=device.id,
        protector_id=protector.id,
        name=name.strip(),
        status="training",
        progress=0,
        audio_url=audio_url,
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)

    # Fish Audio 에 목소리 모델을 만든다. fast 모드라 대개 응답에서 바로 trained 다.
    # 제목에 voice.id 를 넣어 Fish 콘솔에서도 우리 목소리 행과 맞춰 볼 수 있게 한다.
    if fish_audio.is_configured():
        try:
            model_id, state = await fish_audio.create_model(
                f"remory-voice-{voice.id}", content, file.filename
            )
        except fish_audio.FishAudioError as e:
            voice.status = "failed"
            voice.error_message = str(e)[:500]
            db.commit()
            raise APIError(502, "음성 등록 서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.")
        voice.speaker_id = model_id
        db.commit()
        _apply_state(db, voice, device, state)
    # FISH_API_KEY 미설정이면 등록을 건너뛰고 training 상태로 둔다(데모/미연동).

    return envelope(voice_json(voice, device), "음성을 등록했습니다.", 201)


@router.get("/devices/{device_id}/voices")
def list_voices(
    device_id: int,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """등록된 음성 목록 (인형 목소리 선택·학습 상태 표시용)."""
    device = get_owned_device(db, protector, device_id)
    voices = db.scalars(
        select(Voice).where(Voice.device_id == device.id).order_by(Voice.created_at)
    ).all()
    owners = voice_owners(db, voices)
    return envelope(
        [voice_json(v, device, owners.get(v.protector_id)) for v in voices], "OK", 200
    )


@router.get("/voices/{voice_id}/status")
async def get_voice_status(
    voice_id: int,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """음성 학습 상태 조회 (진행 상태 폴링용)."""
    voice = db.get(Voice, voice_id)
    if voice is None:
        raise APIError(404, "음성을 찾을 수 없습니다.")
    device = get_owned_device(db, protector, voice.device_id)  # 권한 없으면 404

    # 아직 학습 중인 모델이면 Fish 에 다시 물어본다. 확인이 안 되면 다음 폴링에 맡긴다.
    if voice.status == "training" and voice.speaker_id and fish_audio.is_configured():
        try:
            state = await fish_audio.get_state(voice.speaker_id)
        except fish_audio.FishAudioError:
            state = None
        if state:
            _apply_state(db, voice, device, state)

    return envelope(
        {
            "voiceId": voice.id,
            "status": voice.status,  # training | ready | failed
            "progress": voice.progress,
            "speakerId": voice.speaker_id,  # ready 면 채워진다
            "errorMessage": voice.error_message,  # failed 면 사유
        },
        "OK",
        200,
    )


@router.delete("/voices/{voice_id}")
async def delete_voice(
    voice_id: int,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """음성 삭제. 기본 음성으로 지정돼 있었다면 지정도 해제한다.

    등록한 본인만 지울 수 있다. 다른 가족도 그 목소리를 골라 쓸 수는 있지만,
    누군가의 목소리를 다른 사람이 없애서는 안 된다.
    """
    voice = db.get(Voice, voice_id)
    if voice is None:
        raise APIError(404, "음성을 찾을 수 없습니다.")
    device = get_owned_device(db, protector, voice.device_id)  # 권한 없으면 404

    # 기본 목소리는 인형이 말할 수단이 없어지므로 지우지 못하게 막는다.
    if voice.protector_id is None:
        raise APIError(400, "기본 목소리는 삭제할 수 없습니다.")
    if voice.protector_id != protector.id:
        raise APIError(403, "직접 등록한 목소리만 삭제할 수 있습니다.")

    # 업로드된 녹음 파일과 Fish Audio 의 목소리 모델도 함께 정리
    if voice.audio_url:
        storage.delete(voice.audio_url)
    if voice.speaker_id:
        await fish_audio.delete_model(voice.speaker_id)

    was_default = device.default_voice_id == voice.id
    db.delete(voice)
    db.flush()

    # 쓰던 목소리를 지웠으면 기본 목소리로 되돌린다 (인형이 벙어리가 되지 않게).
    if was_default:
        device.default_voice_id = None
        ensure_default_voice(db, device)

    db.commit()
    return envelope({"voiceId": voice_id}, "음성을 삭제했습니다.", 200)
