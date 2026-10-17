import datetime as dt
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from .services.storage import normalize_url


class CamelModel(BaseModel):
    """JSON은 camelCase(앱 관례), 내부는 그대로 접근."""

    model_config = ConfigDict(populate_by_name=True)


def _to_storage_url(v):
    """업로드 파일 URL 필드 공통 처리.

    S3 비공개 모드에서 조회 응답은 presigned URL 로 나가는데, 앱이 그 값을
    그대로 되돌려 보낼 수 있다. 서명 쿼리를 떼어 표준 URL 로 되돌린 뒤
    길이 검사를 하도록 BeforeValidator 로 건다(presigned 는 500자를 넘는다).
    """
    return normalize_url(v) if isinstance(v, str) else v


StorageUrl = Annotated[str, BeforeValidator(_to_storage_url)]

# 어르신과의 관계. 가입 마지막 단계(전화번호 인증)와 프로필 수정에서 함께 쓴다.
RELATIONS = {"딸", "아들", "며느리", "사위", "손주", "손녀", "기타"}


def _relation_or_raise(v: Optional[str]) -> Optional[str]:
    if v is not None and v not in RELATIONS:
        raise ValueError(f"관계는 {', '.join(sorted(RELATIONS))} 중 하나여야 합니다.")
    return v


# ── 전화번호 인증 ────────────────────────────────────
class PhoneCodeRequest(CamelModel):
    phone_number: str = Field(alias="phoneNumber", min_length=9, max_length=20)


class PhoneVerifyRequest(CamelModel):
    phone_number: str = Field(alias="phoneNumber", min_length=9, max_length=20)
    code: str = Field(min_length=4, max_length=6)
    # 가입 첫 화면에서 초대 코드를 넣고 들어온 경우. 인증이 끝나는 순간
    # 그 어르신의 가족으로 붙는다(어르신을 따로 등록하지 않는다).
    invite_code: Optional[str] = Field(default=None, alias="inviteCode", max_length=20)
    # 패스키 등록 뒤 '내 정보' 화면에서 받은 값. 이 단계까지는 access token 이
    # 없어 PUT /protectors/me 를 부를 수 없으므로 여기서 함께 저장한다.
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    relation: Optional[str] = Field(default=None, max_length=10)

    @field_validator("relation")
    @classmethod
    def _check_relation(cls, v: Optional[str]) -> Optional[str]:
        return _relation_or_raise(v)


# ── 패스키 등록 ──────────────────────────────────────
class RegistrationOptionsRequest(CamelModel):
    display_name: Optional[str] = Field(default=None, alias="displayName")


class RegistrationRequest(CamelModel):
    credential_id: str = Field(alias="credentialId")
    client_data_json: str = Field(alias="clientDataJSON")
    attestation_object: str = Field(alias="attestationObject")
    # 번호를 먼저 인증한 흐름에서는 여기서 계정이 만들어지므로, 초대 코드도
    # 이 단계에서 받아 가족 연결까지 한 번에 끝낸다.
    invite_code: Optional[str] = Field(default=None, alias="inviteCode", max_length=20)


# ── 패스키 로그인 ────────────────────────────────────
class AuthOptionsRequest(CamelModel):
    # 없으면 discoverable credential(usernameless) 로그인
    phone_number: Optional[str] = Field(default=None, alias="phoneNumber")


class AuthenticationRequest(CamelModel):
    credential_id: str = Field(alias="credentialId")
    client_data_json: str = Field(alias="clientDataJSON")
    authenticator_data: str = Field(alias="authenticatorData")
    signature: str = Field(alias="signature")
    user_handle: Optional[str] = Field(default=None, alias="userHandle")


# ── 토큰 ─────────────────────────────────────────────
class RefreshRequest(CamelModel):
    refresh_token: str = Field(alias="refreshToken")


class LogoutRequest(CamelModel):
    refresh_token: str = Field(alias="refreshToken")


# ── 보호자 프로필 ────────────────────────────────────
class ProtectorUpdateRequest(CamelModel):
    """PUT /protectors/me. 보내지 않은 필드는 변경하지 않는다."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    relation: Optional[str] = Field(default=None, max_length=10)
    profile_image_url: Optional[StorageUrl] = Field(
        default=None, alias="profileImageUrl", max_length=500
    )
    # 인증된 번호와 같을 때만 허용(변경은 전화번호 재인증 필요).
    phone_number: Optional[str] = Field(default=None, alias="phoneNumber", max_length=20)

    @field_validator("relation")
    @classmethod
    def _check_relation(cls, v: Optional[str]) -> Optional[str]:
        return _relation_or_raise(v)


class NotificationSettingsRequest(CamelModel):
    """PATCH /protectors/me/notification-settings. 보낸 항목만 부분 수정."""

    urgent: Optional[bool] = None
    daily_report: Optional[bool] = Field(default=None, alias="dailyReport")
    chat: Optional[bool] = None
    marketing: Optional[bool] = None
    emotion_change: Optional[bool] = Field(default=None, alias="emotionChange")
    device_disconnected: Optional[bool] = Field(default=None, alias="deviceDisconnected")
    medication_missed: Optional[bool] = Field(default=None, alias="medicationMissed")
    voice_request: Optional[bool] = Field(default=None, alias="voiceRequest")
    message_delivered: Optional[bool] = Field(default=None, alias="messageDelivered")
    voice_training_completed: Optional[bool] = Field(
        default=None, alias="voiceTrainingCompleted"
    )
    weekly_report: Optional[bool] = Field(default=None, alias="weeklyReport")
    app_update: Optional[bool] = Field(default=None, alias="appUpdate")


# ── 어르신(User) 정보 ────────────────────────────────
GENDERS = {"female", "male"}
_GENDER_KO = {"여성": "female", "남성": "male", "여": "female", "남": "male"}


class FirebaseVerifyRequest(CamelModel):
    """POST /auth/phone/verify-firebase. 앱이 Firebase 에서 받은 ID 토큰."""

    id_token: str = Field(alias="idToken", min_length=1)
    invite_code: Optional[str] = Field(default=None, alias="inviteCode", max_length=20)
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    relation: Optional[str] = Field(default=None, max_length=10)

    @field_validator("relation")
    @classmethod
    def _check_relation(cls, v: Optional[str]) -> Optional[str]:
        return _relation_or_raise(v)


class UserCreateRequest(CamelModel):
    """POST /users. 가입 플로우에서 어르신을 등록한다."""

    name: str = Field(min_length=1, max_length=50)
    gender: Optional[str] = Field(default=None, max_length=10)
    birth_date: Optional[dt.date] = Field(default=None, alias="birthDate")
    photo_url: Optional[StorageUrl] = Field(default=None, alias="photoUrl", max_length=500)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("gender")
    @classmethod
    def _normalize_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return "male" if v in ("male", "남성", "남") else "female"


class UserUpdateRequest(CamelModel):
    """PUT /users/{userId}. 보내지 않은 필드는 변경하지 않는다."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    gender: Optional[str] = Field(default=None, max_length=10)
    birth_date: Optional[dt.date] = Field(default=None, alias="birthDate")
    photo_url: Optional[StorageUrl] = Field(default=None, alias="photoUrl", max_length=500)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("gender")
    @classmethod
    def _normalize_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = _GENDER_KO.get(v, v).lower()
        if v not in GENDERS:
            raise ValueError("성별은 female 또는 male 이어야 합니다.")
        return v


# ── 인형(Device) 설정 ────────────────────────────────
class DeviceSettingsRequest(CamelModel):
    """PUT /devices/{deviceId}/settings."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=30)
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    default_voice_id: Optional[int] = Field(default=None, alias="defaultVoiceId")
    medication_check: Optional[bool] = Field(default=None, alias="medicationCheck")


class DefaultVoiceRequest(CamelModel):
    """PATCH /devices/{deviceId}/settings/voice."""

    voice_id: int = Field(alias="voiceId")


class FishVoiceImportRequest(CamelModel):
    """POST /devices/{deviceId}/voices/fish — Fish Audio 에 이미 있는 목소리 모델을 가져온다."""

    name: str = Field(min_length=1, max_length=50, description="앱에 보일 이름")
    # 모델 ID(32자리) 또는 fish.audio 의 모델 주소(https://fish.audio/m/<ID>/).
    model_id: str = Field(alias="modelId", min_length=1, max_length=200)


class DndRequest(CamelModel):
    """PUT /devices/{deviceId}/dnd."""

    enabled: Optional[bool] = None
    start_hour: Optional[int] = Field(default=None, alias="startHour", ge=0, le=23)
    end_hour: Optional[int] = Field(default=None, alias="endHour", ge=0, le=23)
    allow_urgent_alert: Optional[bool] = Field(default=None, alias="allowUrgentAlert")
    allow_wake_word: Optional[bool] = Field(default=None, alias="allowWakeWord")


# ── 약 복용 ──────────────────────────────────────────
TIMINGS = {"식전", "식후", "공복", "아무때나"}
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class MedicationCreateRequest(CamelModel):
    """POST /devices/{deviceId}/medications."""

    name: str = Field(min_length=1, max_length=50)
    time: str = Field(pattern=_TIME_PATTERN)  # "08:00"
    timing: str = Field(default="식후", max_length=10)
    enabled: bool = True

    @field_validator("timing")
    @classmethod
    def _check_timing(cls, v: str) -> str:
        if v not in TIMINGS:
            raise ValueError(f"복용 시점은 {', '.join(sorted(TIMINGS))} 중 하나여야 합니다.")
        return v


class MedicationUpdateRequest(CamelModel):
    """PUT /medications/{id}. 보내지 않은 필드는 변경하지 않는다."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    time: Optional[str] = Field(default=None, pattern=_TIME_PATTERN)
    timing: Optional[str] = Field(default=None, max_length=10)
    enabled: Optional[bool] = None

    @field_validator("timing")
    @classmethod
    def _check_timing(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in TIMINGS:
            raise ValueError(f"복용 시점은 {', '.join(sorted(TIMINGS))} 중 하나여야 합니다.")
        return v


# ── 기기(인형)가 올리는 데이터 ───────────────────────
class EmotionCreateRequest(CamelModel):
    """POST /devices/{id}/emotions."""

    emotion: str = Field(min_length=1, max_length=20)


class ActivityCreateRequest(CamelModel):
    """POST /devices/{id}/activities."""

    activity_type: str = Field(alias="activityType", min_length=1, max_length=30)
    content: Optional[str] = None


# 대화 한 줄을 누가 말했는지. 인형은 한 턴을 어르신 말 + 모리 답 두 줄로 보낸다.
SPEAKERS = {"user", "mori"}


class UtteranceItem(CamelModel):
    """대화 한 줄."""

    speaker: str = Field(min_length=1, max_length=10)
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("speaker")
    @classmethod
    def _check_speaker(cls, v: str) -> str:
        if v not in SPEAKERS:
            raise ValueError(f"speaker 는 {', '.join(sorted(SPEAKERS))} 중 하나여야 합니다.")
        return v


class UtteranceCreateRequest(CamelModel):
    """POST /devices/{id}/utterances. 한 턴을 통째로 보낸다(왕복 한 번)."""

    utterances: list[UtteranceItem] = Field(min_length=1, max_length=20)


class ChatDeliveredRequest(CamelModel):
    """POST /devices/{id}/chat/delivered. 인형이 전달·표시 완료한 메시지 id 목록."""

    message_ids: list[int] = Field(alias="messageIds")


class ConversationStateRequest(CamelModel):
    """PATCH /devices/{id}/conversation. 대화 시작(True)/종료(False)."""

    active: bool


# ── 안전 신호 ────────────────────────────────────────
SAFETY_KINDS = {"self_harm", "harm_others", "medical", "abuse", "profanity"}


class SafetyEventRequest(CamelModel):
    """POST /devices/{id}/safety-events. 인형이 대화에서 가려낸 위험 신호."""

    kind: str = Field(min_length=1, max_length=20)
    excerpt: Optional[str] = Field(default=None, max_length=500)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v not in SAFETY_KINDS:
            raise ValueError(f"kind 는 {', '.join(sorted(SAFETY_KINDS))} 중 하나여야 합니다.")
        return v


# ── 푸시 토큰 ────────────────────────────────────────
PUSH_PLATFORMS = {"android", "ios"}


class PushTokenRequest(CamelModel):
    """POST/DELETE /protectors/me/push-tokens. 앱이 받은 FCM 등록 토큰."""

    token: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="android", max_length=10)

    @field_validator("platform")
    @classmethod
    def _check_platform(cls, v: str) -> str:
        if v not in PUSH_PLATFORMS:
            raise ValueError(
                f"platform 은 {', '.join(sorted(PUSH_PLATFORMS))} 중 하나여야 합니다."
            )
        return v


# ── 기기 등록 ────────────────────────────────────────
class DevicePairRequest(CamelModel):
    """POST /devices. 인형을 어르신에게 연결한다."""

    user_id: int = Field(alias="userId")
    serial: Optional[str] = Field(default=None, max_length=64)
    name: Optional[str] = Field(default=None, max_length=30)


# ── 추억 ─────────────────────────────────────────────
class MemoryCreateRequest(CamelModel):
    """POST /users/{id}/memories."""

    image_url: StorageUrl = Field(alias="imageUrl", max_length=500)
    title: str = Field(min_length=1, max_length=100)
    period: Optional[str] = Field(default=None, max_length=50)  # 예: "1980년대"
    description: Optional[str] = None


# ── 가족 대화 ────────────────────────────────────────
class ChatMessageCreateRequest(CamelModel):
    """POST /users/{id}/chat/messages. 글이나 사진 중 하나는 있어야 한다."""

    content: Optional[str] = None
    image_url: Optional[StorageUrl] = Field(default=None, alias="imageUrl", max_length=500)


# ── 음성 ─────────────────────────────────────────────
class VoiceRegisterRequest(CamelModel):
    """POST /devices/{id}/voices (multipart 의 name 필드와 함께 사용)."""

    name: str = Field(min_length=1, max_length=50)
