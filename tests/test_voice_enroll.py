"""목소리 등록(voice-enroll) EC2 측 흐름 테스트.

Fish Audio fast 모드는 모델 생성 응답에서 대개 바로 trained 라 EC2 가 동기로 결과를 받는다.
- register_voice 가 Fish 에 모델을 만들어 성공 시 ready(+speaker_id=모델 ID), 실패 시 failed 로 기록
- 아직 training 이면 상태 조회 때 Fish 에 다시 물어 ready 로 옮기는지
- 상태 조회에 speakerId / errorMessage 가 실리는지
- 삭제하면 Fish 모델도 지우는지
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Device, FamilyMember, Protector, User, Voice
from app.security import create_access_token
from app.services import fish_audio
from app.services.storage import storage

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


client = TestClient(app)


def auth(protector_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(protector_id)}"}


@pytest.fixture
def world():
    """보호자(김지영) + 어르신(박순자) + 인형(모리) + 학습중 목소리 1개."""
    db = TestSession()
    try:
        me = Protector(phone_number="01011112222", display_name="김지영", user_handle=b"h-me")
        db.add(me)
        db.flush()

        user = User(name="박순자", gender="female")
        db.add(user)
        db.flush()
        db.add(FamilyMember(user_id=user.id, protector_id=me.id, is_primary=True))

        device = Device(user_id=user.id, name="모리", battery_level=80, volume=80)
        db.add(device)
        db.flush()

        voice = Voice(
            device_id=device.id, protector_id=me.id, name="딸 지영",
            status="training", progress=0, audio_url="/uploads/voices/x.wav",
        )
        db.add(voice)
        db.flush()

        db.commit()
        return {"me": me.id, "device": device.id, "voice": voice.id}
    finally:
        db.close()


def _upload(device_id: int, protector_id: int):
    return client.post(
        f"/devices/{device_id}/voices",
        headers=auth(protector_id),
        data={"name": "딸 지영"},
        files={"file": ("rec.wav", b"fake-audio-bytes", "audio/wav")},
    )


def test_register_creates_model_and_marks_ready(monkeypatch, world):
    """Fish 모델 생성 성공(trained) → 201 + ready + speaker_id(모델 ID)."""
    monkeypatch.setattr(storage, "save", lambda *a, **k: "/uploads/voices/new.wav")
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)

    called = {}

    async def fake_create(title, audio, filename):
        called.update(title=title, filename=filename, size=len(audio))
        return "fish-model-1", "trained"

    monkeypatch.setattr(fish_audio, "create_model", fake_create)

    r = _upload(world["device"], world["me"])
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["status"] == "ready"
    assert body["speakerId"] == "fish-model-1"
    # 업로드한 파일이 그대로 Fish 로 전달됐는지
    assert called["title"] == f"remory-voice-{body['voiceId']}"
    assert called["size"] == len(b"fake-audio-bytes")


def test_training_model_becomes_ready_on_status_poll(monkeypatch, world):
    """생성 응답이 training 이면 training 으로 두고, 상태 조회 때 trained 면 ready 로 옮긴다."""
    monkeypatch.setattr(storage, "save", lambda *a, **k: "/uploads/voices/new.wav")
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)

    async def fake_create(title, audio, filename):
        return "fish-model-2", "training"

    async def fake_state(model_id):
        assert model_id == "fish-model-2"
        return "trained"

    monkeypatch.setattr(fish_audio, "create_model", fake_create)
    monkeypatch.setattr(fish_audio, "get_state", fake_state)

    body = _upload(world["device"], world["me"]).json()["data"]
    assert body["status"] == "training"
    assert body["speakerId"] == "fish-model-2"

    s = client.get(f"/voices/{body['voiceId']}/status", headers=auth(world["me"])).json()["data"]
    assert s["status"] == "ready"
    assert s["progress"] == 100


def test_failed_model_is_marked_failed(monkeypatch, world):
    """Fish 가 학습 실패(failed)를 주면 failed + 재녹음 안내 사유를 남긴다."""
    monkeypatch.setattr(storage, "save", lambda *a, **k: "/uploads/voices/new.wav")
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)

    async def fake_create(title, audio, filename):
        return "fish-model-3", "failed"

    monkeypatch.setattr(fish_audio, "create_model", fake_create)

    body = _upload(world["device"], world["me"]).json()["data"]
    s = client.get(f"/voices/{body['voiceId']}/status", headers=auth(world["me"])).json()["data"]
    assert s["status"] == "failed"
    assert s["errorMessage"]


def test_delete_removes_fish_model(monkeypatch, world):
    """목소리를 지우면 Fish 의 모델도 지운다."""
    db = TestSession()
    try:
        v = db.get(Voice, world["voice"])
        v.status = "ready"
        v.speaker_id = "fish-model-9"
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(storage, "delete", lambda *a, **k: None)
    deleted = []

    async def fake_delete(model_id):
        deleted.append(model_id)

    monkeypatch.setattr(fish_audio, "delete_model", fake_delete)

    r = client.delete(f"/voices/{world['voice']}", headers=auth(world["me"]))
    assert r.status_code == 200, r.text
    assert deleted == ["fish-model-9"]


def test_register_marks_failed_when_server_unreachable(monkeypatch, world):
    """Fish 호출 실패 시 502 + 해당 voice 는 failed 로 남는다."""
    monkeypatch.setattr(storage, "save", lambda *a, **k: "/uploads/voices/new.wav")
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)

    async def boom(title, audio, filename):
        raise fish_audio.FishAudioError("연결 실패")

    monkeypatch.setattr(fish_audio, "create_model", boom)

    r = _upload(world["device"], world["me"])
    assert r.status_code == 502

    db = TestSession()
    try:
        failed = [v for v in db.query(Voice).all() if v.status == "failed"]
        assert len(failed) == 1
        assert failed[0].error_message
    finally:
        db.close()


def test_register_skips_when_not_configured(monkeypatch, world):
    """FISH_API_KEY 미설정이면 등록을 건너뛰고 training 으로 접수만 한다(데모)."""
    monkeypatch.setattr(storage, "save", lambda *a, **k: "/uploads/voices/new.wav")
    monkeypatch.setattr(fish_audio, "is_configured", lambda: False)

    r = _upload(world["device"], world["me"])
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "training"


def test_status_returns_speaker_and_error(world):
    """상태 조회가 speakerId / errorMessage 를 함께 준다."""
    db = TestSession()
    try:
        v = db.get(Voice, world["voice"])
        v.status = "ready"
        v.speaker_id = "spk_abc123"
        db.commit()
    finally:
        db.close()

    s = client.get(f"/voices/{world['voice']}/status", headers=auth(world["me"])).json()["data"]
    assert s["status"] == "ready"
    assert s["speakerId"] == "spk_abc123"
    assert "errorMessage" in s


def test_ready_voice_alerts_only_the_registrant(monkeypatch, world):
    """목소리가 준비되면 등록한 본인에게만 알린다. 다른 가족은 목록에서 본다."""
    from sqlalchemy import select

    from app.models import Notification
    from app.services import notifications as notif

    db = TestSession()
    try:
        sibling = Protector(
            phone_number="01033334444", display_name="김민수", user_handle=b"h-sib"
        )
        db.add(sibling)
        db.flush()
        user_id = db.get(Device, world["device"]).user_id
        db.add(FamilyMember(user_id=user_id, protector_id=sibling.id))
        db.commit()
        sibling_id = sibling.id
    finally:
        db.close()

    monkeypatch.setattr(storage, "save", lambda *a, **k: "/uploads/voices/new.wav")
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)

    async def fake_create(title, audio, filename):
        return "fish-model-1", "trained"

    monkeypatch.setattr(fish_audio, "create_model", fake_create)
    assert _upload(world["device"], world["me"]).status_code == 201

    db = TestSession()
    try:
        got = [
            (n.protector_id, n.title)
            for n in db.scalars(select(Notification)).all()
        ]
    finally:
        db.close()
    assert got == [(world["me"], notif.VOICE_READY_TITLE)]
    assert sibling_id not in [pid for pid, _ in got]


# ── Fish 에 이미 있는 목소리 모델 가져오기 ─────────────────────────
MODEL_ID = "c2045f5c866b4222a4a2c30b5b051f7e"


def _import(device_id: int, protector_id: int, model_id: str = MODEL_ID, name: str = "차분한 목소리"):
    return client.post(
        f"/devices/{device_id}/voices/fish",
        headers=auth(protector_id),
        json={"name": name, "modelId": model_id},
    )


def _fake_model(state="trained", type_="tts"):
    async def get_model(model_id):
        return {"_id": model_id, "type": type_, "state": state, "title": "x"}

    return get_model


def test_import_fish_model_is_ready_right_away(monkeypatch, world):
    """모델 ID 로 가져오면 녹음 없이 바로 ready, speakerId 는 그 모델 ID."""
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)
    monkeypatch.setattr(fish_audio, "get_model", _fake_model())

    r = _import(world["device"], world["me"])
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["status"] == "ready"
    assert body["speakerId"] == MODEL_ID
    assert body["audioUrl"] is None

    # 가져온 목소리를 인형 기본 목소리로 고를 수 있다.
    r = client.patch(
        f"/devices/{world['device']}/settings/voice",
        headers=auth(world["me"]),
        json={"voiceId": body["voiceId"]},
    )
    assert r.status_code == 200, r.text


def test_import_accepts_fish_page_url(monkeypatch, world):
    """fish.audio 모델 주소를 통째로 붙여 넣어도 ID 를 찾아낸다."""
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)
    monkeypatch.setattr(fish_audio, "get_model", _fake_model())

    r = _import(world["device"], world["me"], f"https://fish.audio/m/{MODEL_ID}/")
    assert r.status_code == 201, r.text
    assert r.json()["data"]["speakerId"] == MODEL_ID


def test_import_rejects_bad_or_unusable_models(monkeypatch, world):
    """ID 꼴이 아니면 400, 없거나 남의 비공개면 404, 학습 전이면 400, 중복이면 409."""
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)

    assert _import(world["device"], world["me"], "not-a-model").status_code == 400

    async def missing(model_id):
        raise fish_audio.FishAudioError("없음", 404)

    monkeypatch.setattr(fish_audio, "get_model", missing)
    assert _import(world["device"], world["me"]).status_code == 404

    monkeypatch.setattr(fish_audio, "get_model", _fake_model(state="training"))
    assert _import(world["device"], world["me"]).status_code == 400

    monkeypatch.setattr(fish_audio, "get_model", _fake_model())
    assert _import(world["device"], world["me"]).status_code == 201
    assert _import(world["device"], world["me"]).status_code == 409


def test_deleting_imported_voice_keeps_fish_model(monkeypatch, world):
    """가져온 목소리는 우리가 만든 게 아니라 지워도 Fish 모델은 지우지 않는다."""
    monkeypatch.setattr(fish_audio, "is_configured", lambda: True)
    monkeypatch.setattr(fish_audio, "get_model", _fake_model())
    voice_id = _import(world["device"], world["me"]).json()["data"]["voiceId"]

    deleted = []

    async def fake_delete(model_id):
        deleted.append(model_id)

    monkeypatch.setattr(fish_audio, "delete_model", fake_delete)
    r = client.delete(f"/voices/{voice_id}", headers=auth(world["me"]))
    assert r.status_code == 200, r.text
    assert deleted == []
