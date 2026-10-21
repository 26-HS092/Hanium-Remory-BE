"""Fish Audio 와 인형 목소리 목록 맞추기.

fish.audio 웹사이트에서 모델을 지우면 앱 목록에도 사라져야 한다. Fish 는 지울 때
알려주지 않으므로, 목록을 보여줄 때 모델이 아직 있는지 물어보고 없어진 목소리를
뺀다. 앱에서 지운 목소리의 Fish 모델은 [remove_voice] 가 함께 지운다.
"""

import asyncio
import logging
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Device, Voice
from . import fish_audio
from .access import ensure_default_voice
from .storage import storage

logger = logging.getLogger("remory.voice_sync")

# 인형 설정은 앱·인형이 자주 읽는다. 기기마다 이 간격에 한 번만 Fish 에 묻는다.
CHECK_INTERVAL_SEC = 60
_last_checked: dict[int, float] = {}


async def remove_voice(db: Session, device: Device, voice: Voice) -> None:
    """목소리를 목록에서 빼고 녹음 파일·Fish 모델도 정리한다. commit 은 부른 쪽이 한다.

    같은 Fish 모델을 다른 목소리(다른 인형 등)가 쓰고 있으면 Fish 쪽은 남긴다.
    남의 모델은 Fish 가 지우기를 거부하므로 그대로 남는다.
    """
    if voice.audio_url:
        storage.delete(voice.audio_url)
    if voice.speaker_id:
        shared = db.scalar(
            select(func.count())
            .select_from(Voice)
            .where(Voice.speaker_id == voice.speaker_id, Voice.id != voice.id)
        )
        if not shared:
            await fish_audio.delete_model(voice.speaker_id)

    was_default = device.default_voice_id == voice.id
    db.delete(voice)
    db.flush()

    # 쓰던 목소리를 지웠으면 기본 목소리로 되돌린다 (인형이 벙어리가 되지 않게).
    if was_default:
        device.default_voice_id = None
        ensure_default_voice(db, device)


async def _gone(model_id: str) -> bool:
    """Fish 에서 모델이 사라졌거나 더는 볼 수 없으면 True. 확인이 안 되면 False(남겨 둔다)."""
    try:
        await fish_audio.get_model(model_id)
    except fish_audio.FishAudioError as e:
        return e.status in (403, 404)
    return False


async def prune_deleted_fish_voices(db: Session, device: Device) -> None:
    """fish.audio 에서 지워진 모델의 목소리를 이 인형 목록에서 뺀다."""
    if not fish_audio.is_configured():
        return
    now = time.monotonic()
    if now - _last_checked.get(device.id, -CHECK_INTERVAL_SEC) < CHECK_INTERVAL_SEC:
        return
    _last_checked[device.id] = now

    # 기본 목소리(protector_id 없음)는 Fish 모델이 아니므로 건드리지 않는다.
    voices = db.scalars(
        select(Voice).where(
            Voice.device_id == device.id,
            Voice.protector_id.is_not(None),
            Voice.speaker_id.is_not(None),
        )
    ).all()
    if not voices:
        return

    gone = await asyncio.gather(*(_gone(v.speaker_id) for v in voices))
    removed = [v for v, g in zip(voices, gone) if g]
    if not removed:
        return

    for voice in removed:
        logger.info(
            "Fish 에서 지워진 목소리를 목록에서 뺀다 voice_id=%s model_id=%s",
            voice.id,
            voice.speaker_id,
        )
        # 이미 Fish 에 없으니 지울 것도 없다. speaker_id 를 비워 삭제 호출을 건너뛴다.
        voice.speaker_id = None
        await remove_voice(db, device, voice)
    db.commit()
