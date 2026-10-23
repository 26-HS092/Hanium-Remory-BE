"""인증 플로우 통합 테스트.

WebAuthn attestation/assertion 검증은 실제 기기가 있어야 하므로
app.services.webauthn_service 의 검증 함수를 가짜로 대체한다.
전화번호 인증 → 패스키 등록 → 로그인 → 토큰 재발급의 배선을 확인한다.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import PhoneVerification
from app.services import webauthn_service as wa

# ── SQLite 테스트 DB ─────────────────────────────────
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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
PHONE = "010-1234-5678"


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _get_stored_code(phone_normalized: str) -> str:
    db = TestSession()
    try:
        rec = db.scalars(
            select(PhoneVerification)
            .where(PhoneVerification.phone_number == phone_normalized)
            .order_by(PhoneVerification.created_at.desc())
        ).first()
        return rec.code
    finally:
        db.close()


def _verify_phone() -> str:
    r = client.post("/auth/phone/verification-code", json={"phoneNumber": PHONE})
    assert r.status_code == 200, r.text
    code = _get_stored_code("01012345678")
    r = client.post("/auth/phone/verify", json={"phoneNumber": PHONE, "code": code})
    assert r.status_code == 200, r.text
    return r.json()["data"]["registrationToken"]


def _register(monkeypatch, reg_token: str):
    headers = {"Authorization": f"Bearer {reg_token}"}
    r = client.post("/auth/passkey/registration/options", json={}, headers=headers)
    assert r.status_code == 200, r.text
    challenge = r.json()["data"]["challenge"]

    class FakeReg:
        credential_public_key = b"\xa5\x01\x02fake-public-key"
        sign_count = 0

    monkeypatch.setattr(wa, "verify_registration", lambda *a, **k: FakeReg())

    client_data = _b64url({"type": "webauthn.create", "challenge": challenge, "origin": "https://remory.app"})
    body = {
        "credentialId": "AAAAcredential-1",
        "clientDataJSON": client_data,
        "attestationObject": "ZmFrZQ",
    }
    r = client.post("/auth/passkey/registration", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_full_signup_and_login(monkeypatch):
    reg_token = _verify_phone()
    data = _register(monkeypatch, reg_token)
    assert data["protectorId"] == 1
    assert data["onboardingCompleted"] is False
    assert data["accessToken"] and data["refreshToken"]

    # ── 로그인 ──
    r = client.post("/auth/passkey/authentication/options", json={"phoneNumber": PHONE})
    assert r.status_code == 200, r.text
    opts = r.json()["data"]
    assert opts["allowCredentials"][0]["id"] == "AAAAcredential-1"
    challenge = opts["challenge"]

    class FakeAuth:
        new_sign_count = 1

    monkeypatch.setattr(wa, "verify_authentication", lambda *a, **k: FakeAuth())
    client_data = _b64url({"type": "webauthn.get", "challenge": challenge, "origin": "https://remory.app"})
    body = {
        "credentialId": "AAAAcredential-1",
        "clientDataJSON": client_data,
        "authenticatorData": "ZmFrZQ",
        "signature": "ZmFrZQ",
    }
    r = client.post("/auth/passkey/authentication", json=body)
    assert r.status_code == 200, r.text
    login = r.json()["data"]
    assert login["protectorId"] == 1

    # ── 토큰 재발급 ──
    r = client.post("/auth/token/refresh", json={"refreshToken": login["refreshToken"]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["accessToken"]

    # 로테이션: 이전 refresh 토큰은 무효
    r = client.post("/auth/token/refresh", json={"refreshToken": login["refreshToken"]})
    assert r.status_code == 401, r.text


def test_wrong_code_rejected():
    client.post("/auth/phone/verification-code", json={"phoneNumber": PHONE})
    r = client.post("/auth/phone/verify", json={"phoneNumber": PHONE, "code": "000000"})
    assert r.status_code == 400
    assert r.json()["data"] is None


def test_faceid_first_signup(monkeypatch):
    """Face-ID-first: 번호 없이 패스키 등록 → onboardingToken → 번호 연결 → 로그인."""
    # 1) 번호 없이 등록 옵션 (토큰 불필요)
    r = client.post("/auth/passkey/registration/options", json={"displayName": "보호자"})
    assert r.status_code == 200, r.text
    challenge = r.json()["data"]["challenge"]

    class FakeReg:
        credential_public_key = b"\xa5\x01\x02fake-public-key"
        sign_count = 0

    monkeypatch.setattr(wa, "verify_registration", lambda *a, **k: FakeReg())
    client_data = _b64url({"type": "webauthn.create", "challenge": challenge, "origin": "https://remory.app"})
    r = client.post("/auth/passkey/registration", json={
        "credentialId": "FACEIDcredential-1",
        "clientDataJSON": client_data,
        "attestationObject": "ZmFrZQ",
    })
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    # 아직 정식 토큰 아님 — onboardingToken만
    assert data["onboardingToken"] and "accessToken" not in data
    onboarding = data["onboardingToken"]

    # 2) 전화번호 인증 → 계정에 번호 연결 + 정식 토큰
    client.post("/auth/phone/verification-code", json={"phoneNumber": PHONE})
    code = _get_stored_code("01012345678")
    r = client.post(
        "/auth/phone/verify",
        json={"phoneNumber": PHONE, "code": code},
        headers={"Authorization": f"Bearer {onboarding}"},
    )
    assert r.status_code == 200, r.text
    tokens = r.json()["data"]
    assert tokens["accessToken"] and tokens["refreshToken"]

    # 3) 이제 그 번호로 로그인 옵션이 credential을 찾아줌
    r = client.post("/auth/passkey/authentication/options", json={"phoneNumber": PHONE})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["allowCredentials"][0]["id"] == "FACEIDcredential-1"


def test_new_device_adds_passkey_to_existing_account(monkeypatch):
    """기기를 바꾸면 번호를 다시 인증하고 새 패스키를 같은 계정에 단다."""
    from app.config import settings

    monkeypatch.setattr(settings, "otp_send_cooldown_sec", 0)
    first = _register(monkeypatch, _verify_phone())

    reg_token = _verify_phone()
    headers = {"Authorization": f"Bearer {reg_token}"}
    r = client.post("/auth/passkey/registration/options", json={}, headers=headers)
    assert r.status_code == 200, r.text
    opts = r.json()["data"]

    client_data = _b64url({"type": "webauthn.create", "challenge": opts["challenge"], "origin": "https://remory.app"})
    body = {
        "credentialId": "AAAAcredential-2",
        "clientDataJSON": client_data,
        "attestationObject": "ZmFrZQ",
    }
    r = client.post("/auth/passkey/registration", json=body, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["protectorId"] == first["protectorId"]

    # 두 기기 패스키 모두 로그인 후보로 나온다.
    r = client.post("/auth/passkey/authentication/options", json={"phoneNumber": PHONE})
    ids = {c["id"] for c in r.json()["data"]["allowCredentials"]}
    assert ids == {"AAAAcredential-1", "AAAAcredential-2"}
