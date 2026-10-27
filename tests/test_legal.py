"""개인정보처리방침 페이지 테스트.

Play 심사는 로그인 없이 열리는 HTML 페이지를 요구한다.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_privacy_is_public_html():
    r = client.get("/privacy")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "개인정보처리방침" in r.text
    assert "iseul9644@gmail.com" in r.text
