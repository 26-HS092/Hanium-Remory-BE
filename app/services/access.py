"""설정 API 공용: 소유권 검사와 응답 직렬화.

보호자는 자신이 가족 멤버로 연결된 어르신(User)과 그 인형(Device)만 조회·수정할 수 있다.
없는 리소스와 권한 없는 리소스를 구분하면 존재 여부가 새므로 둘 다 404로 응답한다.
"""

import datetime as dt
import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import APIError
from ..models import (
    ActivityLog,
    DailyReport,
    Device,
    DndSetting,
    EmotionRecord,
    FamilyChatMessage,
    FamilyMember,
    Medication,
    Memory,
    Notification,
    NotificationSetting,
    Protector,
    User,
    Voice,
    WeeklyReport,
)
from .storage import is_own_image


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(when: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """SQLite에서 읽은 naive datetime을 UTC로 맞춘다."""
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def iso(when: Optional[dt.datetime]) -> Optional[str]:
    aware = _aware(when)
    return aware.isoformat() if aware else None


# ── 소유권 검사 ──────────────────────────────────────
def ensure_own_image(url: Optional[str], protector_id: int) -> None:
    """남이 올린 사진의 URL 을 갖다 붙이지 못하게 막는다.

    업로드 키에 올린 보호자 id 가 들어 있어서 대조만 하면 된다.
    """
    if not is_own_image(url, protector_id):
        raise APIError(400, "직접 올린 사진만 사용할 수 있습니다.")


def get_membership(db: Session, protector_id: int, user_id: int) -> FamilyMember:
    membership = db.scalars(
        select(FamilyMember).where(
            FamilyMember.user_id == user_id, FamilyMember.protector_id == protector_id
        )
    ).first()
    if membership is None:
        raise APIError(404, "연결된 사용자를 찾을 수 없습니다.")
    return membership


def get_owned_user(db: Session, protector: Protector, user_id: int) -> User:
    get_membership(db, protector.id, user_id)
    user = db.get(User, user_id)
    if user is None:
        raise APIError(404, "연결된 사용자를 찾을 수 없습니다.")
    return user


def get_owned_device(db: Session, protector: Protector, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise APIError(404, "인형을 찾을 수 없습니다.")
    membership = db.scalars(
        select(FamilyMember).where(
            FamilyMember.user_id == device.user_id, FamilyMember.protector_id == protector.id
        )
    ).first()
    if membership is None:
        raise APIError(404, "인형을 찾을 수 없습니다.")
    return device


def get_owned_medication(db: Session, protector: Protector, medication_id: int) -> Medication:
    medication = db.get(Medication, medication_id)
    if medication is None:
        raise APIError(404, "약을 찾을 수 없습니다.")
    get_owned_device(db, protector, medication.device_id)  # 권한 없으면 404
    return medication


def primary_membership(db: Session, user_id: int) -> Optional[FamilyMember]:
    return db.scalars(
        select(FamilyMember).where(
            FamilyMember.user_id == user_id, FamilyMember.is_primary.is_(True)
        )
    ).first()


# ── 기본값 보장 ──────────────────────────────────────
def ensure_notification_setting(db: Session, protector: Protector) -> NotificationSetting:
    setting = db.scalars(
        select(NotificationSetting).where(NotificationSetting.protector_id == protector.id)
    ).first()
    if setting is None:
        setting = NotificationSetting(protector_id=protector.id)
        db.add(setting)
        db.flush()
    return setting


DEFAULT_VOICE_NAME = "기본 목소리"


def ensure_default_voice(db: Session, device: Device) -> Voice:
    """기기의 기본 제공 목소리를 보장한다.

    가족이 아직 목소리를 학습시키지 않았어도 인형이 말할 수 있어야 하므로,
    특정 보호자에 속하지 않는(protector_id=None) 기본 음성을 하나 유지한다.
    """
    voice = db.scalars(
        select(Voice).where(Voice.device_id == device.id, Voice.protector_id.is_(None))
    ).first()
    if voice is None:
        voice = Voice(
            device_id=device.id,
            protector_id=None,
            name=DEFAULT_VOICE_NAME,
            status="ready",
            progress=100,
        )
        db.add(voice)
        db.flush()
    # 지정된 기본 음성이 없으면 이 목소리로 채워둔다.
    if device.default_voice_id is None:
        device.default_voice_id = voice.id
    return voice


def ensure_dnd(db: Session, device: Device) -> DndSetting:
    dnd = db.scalars(select(DndSetting).where(DndSetting.device_id == device.id)).first()
    if dnd is None:
        dnd = DndSetting(device_id=device.id)
        db.add(dnd)
        db.flush()
    return dnd


# ── 직렬화 ───────────────────────────────────────────
def notification_settings_json(setting: NotificationSetting) -> dict:
    return {
        "urgent": setting.urgent,
        "dailyReport": setting.daily_report,
        "chat": setting.chat,
        "marketing": setting.marketing,
        "emotionChange": setting.emotion_change,
        "deviceDisconnected": setting.device_disconnected,
        "medicationMissed": setting.medication_missed,
        "voiceRequest": setting.voice_request,
        "messageDelivered": setting.message_delivered,
        "voiceTrainingCompleted": setting.voice_training_completed,
        "weeklyReport": setting.weekly_report,
        "appUpdate": setting.app_update,
    }


def calc_age(birth_date: Optional[dt.date]) -> Optional[int]:
    """만 나이."""
    if birth_date is None:
        return None
    today = _now().date()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def user_json(user: User, device: Optional[Device] = None) -> dict:
    return {
        "userId": user.id,
        "name": user.name,
        "gender": user.gender,
        "birthDate": user.birth_date.isoformat() if user.birth_date else None,
        "age": calc_age(user.birth_date),
        "photoUrl": user.photo_url,
        "note": user.note,
        "deviceId": device.id if device else None,
        "createdAt": iso(user.created_at),
    }


def is_connected(device: Device) -> bool:
    """마지막 heartbeat가 임계 시간 안이면 연결된 것으로 본다."""
    last = _aware(device.last_heartbeat_at)
    if last is None:
        return False
    return (_now() - last).total_seconds() <= settings.device_offline_after_sec


def battery_hours_left(device: Device) -> int:
    return round(device.battery_level / 100 * settings.device_battery_full_hours)


def voice_json(
    voice: Voice, device: Device, owner: Optional[Protector] = None
) -> dict:
    return {
        "voiceId": voice.id,
        "name": voice.name,
        "protectorId": voice.protector_id,
        # 누가 등록했는지. 목소리는 등록한 사람 것이 아니라 그 인형 것이라
        # 가족 모두에게 보이는데, 이름만 있으면 '내가 올린 것' 으로 오해하기
        # 쉽다. 앱이 '딸 김민지 님이 등록' 이라고 적을 수 있게 함께 준다.
        "ownerName": owner.display_name if owner else None,
        "ownerRelation": owner.relation if owner else None,
        "status": voice.status,
        "progress": voice.progress,
        "speakerId": voice.speaker_id,
        "isDefault": device.default_voice_id == voice.id,
        # 앱에서 등록한 목소리를 다시 들어볼 수 있게 준다.
        # envelope() 이 presigned URL 로 바꿔 내보낸다.
        "audioUrl": voice.audio_url,
    }


def voice_owners(db: Session, voices: list) -> dict:
    """목소리를 등록한 보호자들을 한 번에 읽어 온다(목소리마다 따로 묻지 않게)."""
    ids = {v.protector_id for v in voices if v.protector_id is not None}
    if not ids:
        return {}
    return {
        p.id: p
        for p in db.scalars(select(Protector).where(Protector.id.in_(ids))).all()
    }


def device_json(db: Session, device: Device) -> dict:
    voices = db.scalars(
        select(Voice).where(Voice.device_id == device.id).order_by(Voice.created_at)
    ).all()
    owners = voice_owners(db, voices)
    return {
        "deviceId": device.id,
        "userId": device.user_id,
        "name": device.name,
        "serial": device.serial,
        "connected": is_connected(device),
        # 실제 대화 중인지. 인형이 죽어(하트비트 끊김) 값이 남아있어도 false 로 본다.
        "inConversation": device.in_conversation and is_connected(device),
        "batteryLevel": device.battery_level,
        "batteryHoursLeft": battery_hours_left(device),
        "lastHeartbeatAt": iso(device.last_heartbeat_at),
        "volume": device.volume,
        "medicationCheck": device.medication_check,
        # 값은 발급 응답에서만 나간다. 여기서는 앱이 "발급하기/재발급" 중
        # 무엇을 보여줄지 정할 수 있게 발급 여부만 알린다.
        "hasDeviceToken": device.device_token is not None,
        "defaultVoiceId": device.default_voice_id,
        "voices": [voice_json(v, device, owners.get(v.protector_id)) for v in voices],
        "pairedAt": iso(device.created_at),
    }


def dnd_json(dnd: DndSetting) -> dict:
    return {
        "deviceId": dnd.device_id,
        "enabled": dnd.enabled,
        "startHour": dnd.start_hour,
        "endHour": dnd.end_hour,
        "allowUrgentAlert": dnd.allow_urgent_alert,
        "allowWakeWord": dnd.allow_wake_word,
    }


def medication_json(medication: Medication) -> dict:
    return {
        "medicationId": medication.id,
        "deviceId": medication.device_id,
        "name": medication.name,
        "time": medication.time,
        "timing": medication.timing,
        "enabled": medication.enabled,
        "createdAt": iso(medication.created_at),
    }


# ── 기능(홈·추억·대화·감정·활동·알림·리포트) 직렬화 ──────────
def memory_json(memory: Memory) -> dict:
    return {
        "memoryId": memory.id,
        "imageUrl": memory.image_url,
        # 인형은 사진 내용을 읽을 때 이것을 쓴다. 예전 추억은 자르기 전 사진이 없다.
        "originalImageUrl": memory.original_image_url or memory.image_url,
        "title": memory.title,
        "period": memory.period,
        "description": memory.description,
        "createdAt": iso(memory.created_at),
    }


def emotion_json(record: EmotionRecord) -> dict:
    return {
        "emotionId": record.id,
        "emotion": record.emotion,
        "createdAt": iso(record.created_at),
    }


def activity_json(log: ActivityLog) -> dict:
    return {
        "activityId": log.id,
        "activityType": log.activity_type,
        "content": log.content,
        "createdAt": iso(log.created_at),
    }


def notification_json(notification: Notification) -> dict:
    return {
        "notificationId": notification.id,
        "type": notification.type,
        "title": notification.title,
        "content": notification.content,
        "isRead": notification.is_read,
        "createdAt": iso(notification.created_at),
    }


def chat_message_json(message: FamilyChatMessage) -> dict:
    return {
        "messageId": message.id,
        "senderType": message.sender_type,
        "senderId": message.sender_id,
        "content": message.content,
        "imageUrl": message.image_url,
        # 어르신 답장의 목소리. envelope() 이 presigned URL 로 바꿔 내보낸다.
        "audioUrl": message.audio_url,
        # 인형이 어르신께 읽어드렸는지. 앱이 '여기까지 읽어드렸어요' 를
        # 어디에 놓을지 이 값으로 정한다.
        "deliveredToDevice": message.delivered_to_device,
        "createdAt": iso(message.created_at),
    }


def _excerpt_json(raw: Optional[str]) -> list:
    """리포트에 담아 둔 대화 발췌를 배열로 돌려준다.

    예전 리포트에는 없고(None), 배치가 남긴 글이 깨져 있을 수도 있다.
    그때는 빈 배열이다 — 발췌 하나 때문에 리포트가 안 열려서는 안 된다.
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def daily_report_json(report: DailyReport) -> dict:
    return {
        "reportId": report.id,
        "reportDate": report.report_date.isoformat() if report.report_date else None,
        "conversationCount": report.conversation_count,
        "familyInteractionCount": report.family_interaction_count,
        "emotionSummary": report.emotion_summary,
        "summary": report.summary,
        "excerpt": _excerpt_json(report.excerpt),
        "dayStory": report.day_story,
        "suggestion": report.suggestion,
        "createdAt": iso(report.created_at),
    }


def weekly_report_json(report: WeeklyReport) -> dict:
    return {
        "reportId": report.id,
        "weekStart": report.week_start.isoformat() if report.week_start else None,
        "totalConversationCount": report.total_conversation_count,
        "familyInteractionCount": report.family_interaction_count,
        "avgEmotionScore": report.avg_emotion_score,
        "dominantEmotion": report.dominant_emotion,
        "emergencyAlertCount": report.emergency_alert_count,
        "weeklySummary": report.weekly_summary,
        "weekStory": report.week_story,
        "keywords": _excerpt_json(report.keywords),
        "dailyEmotions": _excerpt_json(report.daily_emotions),
        "createdAt": iso(report.created_at),
    }
