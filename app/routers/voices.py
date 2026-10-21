"""가족 목소리 등록(Fish Audio 보이스 클로닝)·상태 조회·삭제.

흐름: 가족이 녹음 업로드 → EC2 가 Fish Audio 에 비공개 목소리 모델 생성(fast, 보통 즉시)
      → status=ready(+speaker_id=모델 ID) → PATCH /devices/{id}/settings/voice 로 기본 음성 지정.
      생성 응답이 아직 trained 가 아니면 training 으로 두고, 상태 조회 때 Fish 에 다시 묻는다.
Fish 에 이미 있는 목소리 모델(라이브러리·직접 만든 것)은 모델 ID 로 가져와 고를 수 있다.
fish.audio 에서 모델을 지우면 목록을 볼 때 앱에서도 빠지고, 앱에서 지우면 Fish 모델도 지운다.
음성은 기기(device)에 묶이고, 누가 녹음했는지는 protector_id 로 남는다.
"""

import os
import re

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_protector
from ..errors import APIError, envelope
from ..models import Protector, Voice
from ..schemas import FishVoiceImportRequest
from ..services import fish_audio
from ..services.access import (
    get_owned_device,
    voice_json,
    voice_owners,
)
from ..services.notifications import notify_voice_ready
from ..services.storage import storage
from ..services.voice_sync import prune_deleted_fish_voices, remove_voice

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


# Fish 모델 ID 는 32자리 16진수다. fish.audio/m/<ID>/ 주소를 붙여 넣어도 찾아낸다.
FISH_MODEL_ID = re.compile(r"[0-9a-f]{32}")


@router.post("/devices/{device_id}/voices/fish", status_code=201)
async def import_fish_voice(
    device_id: int,
    body: FishVoiceImportRequest,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """Fish Audio 에 이미 있는 목소리 모델을 인형 목소리 목록에 가져온다.

    녹음 없이 모델 ID 만으로 들인다. 바로 쓸 수 있는(trained) TTS 모델만 받는다.
    """
    device = get_owned_device(db, protector, device_id)
    if not fish_audio.is_configured():
        raise APIError(503, "Fish Audio 가 아직 설정되지 않았습니다.")

    match = FISH_MODEL_ID.search(body.model_id.strip().lower())
    if match is None:
        raise APIError(400, "Fish 모델 ID 를 확인해 주세요.")
    model_id = match.group(0)

    exists = db.scalars(
        select(Voice).where(Voice.device_id == device.id, Voice.speaker_id == model_id)
    ).first()
    if exists is not None:
        raise APIError(409, f"이미 '{exists.name}' 으로 등록된 목소리예요.")

    try:
        model = await fish_audio.get_model(model_id)
    except fish_audio.FishAudioError as e:
        if e.status in (403, 404):
            raise APIError(404, "그 모델을 찾을 수 없어요. 공개 모델이거나 우리 계정의 모델이어야 해요.")
        raise APIError(502, "음성 등록 서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.")
    if model.get("type") != "tts":
        raise APIError(400, "말하기(TTS)용 모델만 가져올 수 있어요.")
    if model.get("state") != "trained":
        raise APIError(400, "아직 학습이 끝나지 않은 모델이에요.")

    voice = Voice(
        device_id=device.id,
        protector_id=protector.id,
        name=body.name.strip(),
        status="ready",
        progress=100,
        speaker_id=model_id,
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)
    return envelope(voice_json(voice, device), "목소리를 가져왔습니다.", 201)


@router.get("/devices/{device_id}/voices")
async def list_voices(
    device_id: int,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """등록된 음성 목록 (인형 목소리 선택·학습 상태 표시용)."""
    device = get_owned_device(db, protector, device_id)
    await prune_deleted_fish_voices(db, device)
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

    # 녹음 파일과 Fish 모델도 함께 지워 fish.audio 에서도 사라지게 한다.
    await remove_voice(db, device, voice)

    db.commit()
    return envelope({"voiceId": voice_id}, "음성을 삭제했습니다.", 200)
