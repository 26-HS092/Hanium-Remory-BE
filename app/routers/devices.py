"""인형(모리) 상태·설정, 방해 금지 시간, 약 목록."""

from typing import Optional

import os

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

import secrets

from ..database import get_db
from ..deps import get_current_device, get_current_protector
from ..errors import APIError, envelope
from ..models import (
    ActivityLog,
    Device,
    EmotionRecord,
    FamilyChatMessage,
    FamilyMember,
    Medication,
    Memory,
    Protector,
    SafetyEvent,
    User,
    Utterance,
    Voice,
    utcnow,
)
from ..schemas import (
    ActivityCreateRequest,
    ChatDeliveredRequest,
    ConversationStateRequest,
    DefaultVoiceRequest,
    DevicePairRequest,
    DeviceSettingsRequest,
    DndRequest,
    EmotionCreateRequest,
    MedicationCreateRequest,
    SafetyEventRequest,
    UtteranceCreateRequest,
)
from ..services.access import (
    chat_message_json,
    device_json,
    dnd_json,
    ensure_default_voice,
    ensure_dnd,
    get_owned_device,
    get_owned_user,
    medication_json,
    memory_json,
)
from ..services.emotion_codes import normalize_emotion
from ..services.storage import storage
from ..services.voice_sync import prune_deleted_fish_voices
from ..services.notifications import (
    notify_elder_reply,
    notify_message_delivered,
    notify_negative_emotion,
    notify_self_harm,
    notify_reconnected,
)

router = APIRouter(prefix="/devices", tags=["devices"])


def _get_voice(db: Session, device_id: int, voice_id: int) -> Voice:
    voice = db.get(Voice, voice_id)
    if voice is None or voice.device_id != device_id:
        raise APIError(404, "목소리를 찾을 수 없습니다.")
    if voice.status != "ready":
        raise APIError(400, "아직 학습이 끝나지 않은 목소리입니다.")
    return voice


def authorize_device_read(
    device_id: int,
    x_device_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Device:
    """이 기기를 조회할 권한을 확인한다. 인형(기기 토큰)과 보호자(JWT) 둘 다 허용한다.

    인형은 자기 설정·방해금지·약 목록을 X-Device-Token 으로 읽고, 앱(보호자)은 JWT 로 조회한다.
    """
    if x_device_token:
        device = db.scalars(
            select(Device).where(Device.device_token == x_device_token)
        ).first()
        if device is None or device.id != device_id:
            raise APIError(401, "유효하지 않은 기기 토큰입니다.")
        return device
    protector = get_current_protector(authorization=authorization, db=db)
    return get_owned_device(db, protector, device_id)


# ── 기기 등록 ────────────────────────────────────────
@router.post("", status_code=201)
def pair_device(
    body: DevicePairRequest,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """인형(모리) 기기를 어르신에게 연결한다 (등록 3/3).

    등록과 동시에 기본 목소리를 만들어 두어, 가족이 목소리를 학습시키기 전에도
    인형이 말할 수 있게 한다.
    """
    user = get_owned_user(db, protector, body.user_id)

    if body.serial:
        exists = db.scalars(select(Device).where(Device.serial == body.serial)).first()
        if exists is not None:
            raise APIError(409, "이미 등록된 기기입니다.")

    device = Device(
        user_id=user.id,
        name=body.name or "모리",
        serial=body.serial,
        device_token=secrets.token_urlsafe(32),
    )
    db.add(device)
    db.flush()

    ensure_default_voice(db, device)  # 기본 목소리 + 기본 음성 지정
    ensure_dnd(db, device)
    db.commit()
    db.refresh(device)

    return envelope(device_json(db, device), "기기를 등록했습니다.", 201)


@router.post("/{device_id}/token", status_code=201)
def issue_device_token(
    device_id: int,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """인형이 서버에 데이터를 올릴 때 쓰는 X-Device-Token 을 새로 발급한다.

    등록할 때도 토큰이 만들어지지만 페어링 응답에는 담지 않는다(가족 전원이 보는
    조회 응답에 자격증명이 섞이지 않게 하려고). 인형에 값을 넣어 줄 때는 여기서 받는다.

    발급하는 순간 이전 토큰은 무효가 되므로 유출됐을 때 회전 수단으로도 쓴다.
    반환된 값은 다시 조회할 수 없으니 인형에 바로 넣어야 한다.
    """
    device = get_owned_device(db, protector, device_id)

    device.device_token = secrets.token_urlsafe(32)
    db.commit()

    return envelope(
        {"deviceId": device.id, "deviceToken": device.device_token},
        "새 기기 토큰을 발급했습니다. 이 값은 다시 볼 수 없습니다.",
        201,
    )


# ── 인형 상태·설정 ───────────────────────────────────
@router.get("/{device_id}/settings")
async def get_device_settings(
    device_id: int,
    device: Device = Depends(authorize_device_read),
    db: Session = Depends(get_db),
):
    """연결 상태·배터리·등록 음성·볼륨·기본 음성. 앱(JWT)·인형(기기 토큰) 모두 조회."""
    # fish.audio 에서 지운 목소리는 여기서도 빠지게 한다(인형도 지워진 모델로 말하지 않게).
    await prune_deleted_fish_voices(db, device)
    return envelope(device_json(db, device), "OK", 200)


@router.put("/{device_id}/settings")
def update_device_settings(
    device_id: int,
    body: DeviceSettingsRequest,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """인형 이름·볼륨·기본 음성·복용 확인 여부 수정."""
    device = get_owned_device(db, protector, device_id)
    fields = body.model_dump(exclude_unset=True)

    if "name" in fields and fields["name"] is not None:
        device.name = fields["name"].strip()
    if "volume" in fields and fields["volume"] is not None:
        device.volume = fields["volume"]
    if "medication_check" in fields and fields["medication_check"] is not None:
        device.medication_check = fields["medication_check"]
    if "default_voice_id" in fields:
        voice_id = fields["default_voice_id"]
        if voice_id is None:
            device.default_voice_id = None
        else:
            device.default_voice_id = _get_voice(db, device.id, voice_id).id

    db.commit()
    return envelope(device_json(db, device), "인형 설정을 저장했습니다.", 200)


@router.patch("/{device_id}/settings/voice")
def set_default_voice(
    device_id: int,
    body: DefaultVoiceRequest,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """목록에서 특정 음성을 기본 음성으로 지정."""
    device = get_owned_device(db, protector, device_id)
    voice = _get_voice(db, device.id, body.voice_id)
    device.default_voice_id = voice.id
    db.commit()
    return envelope(
        {"deviceId": device.id, "defaultVoiceId": voice.id, "name": voice.name},
        "기본 목소리를 변경했습니다.",
        200,
    )


# ── 방해 금지 시간 ───────────────────────────────────
@router.get("/{device_id}/dnd")
def get_dnd(
    device_id: int,
    device: Device = Depends(authorize_device_read),
    db: Session = Depends(get_db),
):
    """방해 금지 시간 조회. 설정한 적 없으면 기본값(23시~7시) 생성. 앱·인형 모두 조회."""
    dnd = ensure_dnd(db, device)
    db.commit()
    return envelope(dnd_json(dnd), "OK", 200)


@router.put("/{device_id}/dnd")
def update_dnd(
    device_id: int,
    body: DndRequest,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """방해 금지 시간 수정. 시작·종료 시각이 같으면 종일이 되므로 막는다."""
    device = get_owned_device(db, protector, device_id)
    dnd = ensure_dnd(db, device)
    fields = body.model_dump(exclude_unset=True)

    for key in (
        "enabled",
        "start_hour",
        "end_hour",
        "allow_urgent_alert",
        "allow_wake_word",
    ):
        if key in fields and fields[key] is not None:
            setattr(dnd, key, fields[key])

    if dnd.start_hour == dnd.end_hour:
        raise APIError(400, "시작 시각과 종료 시각이 같을 수 없습니다.")

    db.commit()
    return envelope(dnd_json(dnd), "방해 금지 시간을 저장했습니다.", 200)


# ── 약 복용 시간 ─────────────────────────────────────
@router.get("/{device_id}/medications")
def list_medications(
    device_id: int,
    device: Device = Depends(authorize_device_read),
    db: Session = Depends(get_db),
):
    """약 복용 목록 조회. 앱(보호자)은 JWT, 인형(기기)은 X-Device-Token 으로 조회한다."""
    rows = db.scalars(
        select(Medication)
        .where(Medication.device_id == device.id)
        .order_by(Medication.time, Medication.id)
    ).all()
    return envelope(
        {
            "deviceId": device.id,
            "medicationCheck": device.medication_check,
            "medications": [medication_json(m) for m in rows],
        },
        "OK",
        200,
    )


@router.post("/{device_id}/medications", status_code=201)
def create_medication(
    device_id: int,
    body: MedicationCreateRequest,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """약 추가."""
    device = get_owned_device(db, protector, device_id)
    medication = Medication(
        device_id=device.id,
        name=body.name.strip(),
        time=body.time,
        timing=body.timing,
        enabled=body.enabled,
    )
    db.add(medication)
    db.commit()
    return envelope(medication_json(medication), "약을 추가했습니다.", 201)


# ── 기기(인형)가 스스로 호출하는 API ─────────────────────
# 사람(보호자)이 아니라 인형이 X-Device-Token 으로 인증한다.
@router.patch("/{device_id}/heartbeat")
def heartbeat(
    device_id: int,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형이 살아있음을 알린다(주기적 호출). 마지막 연결 시각만 갱신한다.

    """
    # 토큰이 가리키는 기기와 URL 의 기기가 다르면 막는다.
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    previous_heartbeat = device.last_heartbeat_at
    device.last_heartbeat_at = utcnow()
    db.commit()

    # 끊긴 동안에는 인형이 아무것도 못 보내므로, 돌아온 지금이 알릴 수 있는 시점이다.
    notify_reconnected(db, device, previous_heartbeat)

    return envelope({"deviceId": device.id, "connected": True}, "OK", 200)


@router.post("/{device_id}/emotions", status_code=201)
def create_emotion(
    device_id: int,
    body: EmotionCreateRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형이 감지한 어르신 감정을 기록한다."""
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    # 인형은 '중립'처럼 한글로 보내는데 앱 그래프와 알림은 영문 코드를 쓴다.
    # 여기서 맞춰 두지 않으면 저장은 돼도 그래프가 평평하게만 나온다.
    emotion = normalize_emotion(body.emotion)
    if emotion is None:
        raise APIError(400, f"알 수 없는 감정입니다: {body.emotion}")

    record = EmotionRecord(user_id=device.user_id, emotion=emotion)
    db.add(record)
    db.commit()
    db.refresh(record)

    notify_negative_emotion(db, device.user_id, emotion)

    return envelope(
        {"emotionId": record.id, "userId": device.user_id},
        "감정을 기록했습니다.",
        201,
    )


@router.post("/{device_id}/activities", status_code=201)
def create_activity(
    device_id: int,
    body: ActivityCreateRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형이 어르신 활동(대화·복약 등)을 기록한다."""
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    log = ActivityLog(
        user_id=device.user_id,
        activity_type=body.activity_type,
        content=body.content,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return envelope(
        {"activityId": log.id, "userId": device.user_id},
        "활동을 기록했습니다.",
        201,
    )


@router.post("/{device_id}/utterances", status_code=201)
def create_utterances(
    device_id: int,
    body: UtteranceCreateRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형이 어르신과 주고받은 말을 기록한다(한 턴 통째로).

    리포트를 만드는 재료다. 앱으로 다시 나가지 않으며, 7일이 지나면
    데일리 배치가 지운다(scripts/generate_daily_reports.py).
    """
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    rows = [
        Utterance(user_id=device.user_id, speaker=u.speaker, content=u.content)
        for u in body.utterances
    ]
    db.add_all(rows)
    db.commit()
    return envelope(
        {"userId": device.user_id, "saved": len(rows)},
        "발화를 기록했습니다.",
        201,
    )


@router.post("/{device_id}/safety-events", status_code=201)
def create_safety_event(
    device_id: int,
    body: SafetyEventRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형이 대화에서 가려낸 위험 신호를 기록한다.

    자해 신호만 가족에게 바로 알린다. 타해(공격성)와 학대 정황은 기록만 남긴다.
    공격성은 치매에서 흔한 증상이라 매번 알리면 어르신을 고발하는 꼴이 되고,
    학대 정황은 기록만 남긴다 — 사실
    확인이 안 된 이야기이고, 실시간 알림이 의심받는 사람에게 그대로 가면
    어르신이 오히려 위험해질 수 있다. 대신 리포트에 담겨 가족 모두가 본다.
    """
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    event = SafetyEvent(
        user_id=device.user_id, kind=body.kind, excerpt=body.excerpt
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    alerted = 0
    if body.kind == "self_harm":
        alerted = notify_self_harm(db, device.user_id, body.excerpt or "")

    return envelope(
        {"safetyEventId": event.id, "userId": device.user_id, "alerted": alerted},
        "안전 신호를 기록했습니다.",
        201,
    )


@router.get("/{device_id}/chat/pending")
def pending_chat_messages(
    device_id: int,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형에 아직 전달 안 한 가족 메시지(글·사진)를 오래된 순으로 준다.

    가족(보호자)이 보낸 것만 대상이며, 인형이 말/화면으로 전한 뒤
    /chat/delivered 로 표시하면 다음부터 빠진다.
    """
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    rows = db.scalars(
        select(FamilyChatMessage)
        .where(
            FamilyChatMessage.user_id == device.user_id,
            FamilyChatMessage.sender_type == "protector",
            FamilyChatMessage.delivered_to_device.is_(False),
        )
        .order_by(FamilyChatMessage.created_at, FamilyChatMessage.id)
    ).all()

    # 인형이 "딸 OOO에게서 메시지가 왔어요" 라고 말할 수 있도록 보낸 사람 정보를 붙인다.
    sender_ids = {m.sender_id for m in rows if m.sender_id is not None}
    senders = {
        p.id: p
        for p in db.scalars(select(Protector).where(Protector.id.in_(sender_ids))).all()
    } if sender_ids else {}
    messages = []
    for m in rows:
        item = chat_message_json(m)
        p = senders.get(m.sender_id)
        item["senderName"] = p.display_name if p else None
        item["senderRelation"] = p.relation if p else None
        messages.append(item)
    return envelope({"messages": messages}, "OK", 200)


@router.post("/{device_id}/chat/delivered")
def mark_chat_delivered(
    device_id: int,
    body: ChatDeliveredRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형이 전달(글 재생)·표시(사진)를 마친 메시지를 전달 완료로 표시한다."""
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    rows = db.scalars(
        select(FamilyChatMessage).where(
            FamilyChatMessage.id.in_(body.message_ids),
            FamilyChatMessage.user_id == device.user_id,
        )
    ).all()
    # 이번에 처음 전해진 것만 알린다. 인형이 같은 id 를 다시 올려도(응답을 못
    # 받아 재시도 등) 알림이 또 가지 않게.
    newly_delivered_senders = {
        m.sender_id
        for m in rows
        if not m.delivered_to_device
        and m.sender_type == "protector"
        and m.sender_id is not None
    }
    for m in rows:
        m.delivered_to_device = True
        if m.image_url:
            m.displayed_on_device = True
    db.commit()

    notify_message_delivered(db, device.user_id, newly_delivered_senders)
    return envelope({"deliveredCount": len(rows)}, "OK", 200)


# 어르신 답장 녹음. 인형은 wav 로 올리지만 다른 형식이 와도 받는다.
REPLY_AUDIO_PREFIX = "chat-replies"
REPLY_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".webm"}
REPLY_AUDIO_MAX_BYTES = 10 * 1024 * 1024  # 10MB — 1분짜리 wav 도 2MB 안팎이다


@router.post("/{device_id}/chat/reply", status_code=201)
async def post_elder_reply(
    device_id: int,
    content: str = Form(..., max_length=1000, description="받아 적은 답장"),
    audio: Optional[UploadFile] = File(None, description="답장 목소리 녹음"),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """어르신이 가족 메시지를 듣고 하신 답장을 대화방에 올린다.

    인형이 말씀을 받아 적은 글과, 말씀하신 목소리를 함께 올린다. 보낸 사람은
    어르신(sender_type 'user')이고, 가족 모두에게 알린다.

    목소리는 가족에게 보낸 이 답장만 남는다. 평소 대화 녹음은 인형 밖으로
    나가지 않는다.
    """
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")
    if device.user_id is None:
        raise APIError(409, "어르신과 연결되지 않은 인형입니다.")

    text = content.strip()
    if not text:
        raise APIError(400, "답장 내용이 비어 있습니다.")

    audio_url = None
    if audio is not None and audio.filename:
        ext = os.path.splitext(audio.filename)[1].lower()
        if ext not in REPLY_AUDIO_EXT:
            raise APIError(400, "음성 파일만 올릴 수 있습니다.")
        data = await audio.read()
        if len(data) > REPLY_AUDIO_MAX_BYTES:
            raise APIError(400, "10MB 이하의 음성만 올릴 수 있습니다.")
        if data:
            audio_url = storage.save(data, ext, prefix=REPLY_AUDIO_PREFIX)

    message = FamilyChatMessage(
        user_id=device.user_id,
        sender_type="user",
        sender_id=None,
        content=text,
        audio_url=audio_url,
        # 어르신이 직접 하신 말씀이라 인형이 다시 읽어드릴 것이 없다.
        delivered_to_device=True,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    notify_elder_reply(db, device.user_id, text)
    return envelope(chat_message_json(message), "답장을 올렸습니다.", 201)


@router.patch("/{device_id}/conversation")
def set_conversation_state(
    device_id: int,
    body: ConversationStateRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """대화 시작(active=true)/종료(active=false)를 알린다.

    앱은 '연결됨'과 '대화중'을 구분해 표시한다.
    """
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    device.in_conversation = body.active
    db.commit()
    return envelope(
        {"deviceId": device.id, "inConversation": device.in_conversation},
        "OK",
        200,
    )


@router.get("/{device_id}/memories")
def device_rag_data(
    device_id: int,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """인형이 RAG에 쓸 어르신 데이터(프로필·가족·사진 추억)를 준다.

    사진 URL 은 envelope 에서 presigned 로 나가므로 인형이 바로 내려받을 수 있다.
    """
    if device.id != device_id:
        raise APIError(403, "다른 기기의 토큰입니다.")

    user = db.get(User, device.user_id)
    memories = db.scalars(
        select(Memory)
        .where(Memory.user_id == device.user_id)
        .order_by(Memory.created_at)
    ).all()

    members = db.scalars(
        select(FamilyMember).where(FamilyMember.user_id == device.user_id)
    ).all()
    family = []
    for fm in members:
        protector = db.get(Protector, fm.protector_id)
        if protector is not None:
            family.append({"relation": protector.relation, "name": protector.display_name})

    return envelope(
        {
            "user": {
                "name": user.name if user else None,
                "gender": user.gender if user else None,
                "birthDate": user.birth_date.isoformat() if (user and user.birth_date) else None,
                "note": user.note if user else None,
            },
            "family": family,
            "memories": [memory_json(m) for m in memories],
        },
        "OK",
        200,
    )
