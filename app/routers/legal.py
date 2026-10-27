"""스토어와 앱에서 링크하는 공개 문서.

Play 는 개인정보처리방침을 로그인 없이 열리는 일반 웹페이지로 요구한다.
봉투(envelope) 없이 HTML 그대로 내려준다.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

_DIR = Path(__file__).resolve().parent.parent / "legal"


@router.get("/privacy", response_class=HTMLResponse)
def privacy():
    """개인정보처리방침."""
    return HTMLResponse((_DIR / "privacy.html").read_text(encoding="utf-8"))
