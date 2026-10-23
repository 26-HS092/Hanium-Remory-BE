"""앱-도메인 연결 파일 서빙.

실기기 패스키(WebAuthn) 등록 전에 OS가 rp_id 도메인의 아래 경로를 확인한다.
반드시 HTTPS로, 봉투(envelope) 없이 '날것 JSON'으로 응답해야 한다.
  iOS     : GET /.well-known/apple-app-site-association
  Android : GET /.well-known/assetlinks.json
값은 .env 의 IOS_APP_IDS / ANDROID_* 로 채운다.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import settings

logger = logging.getLogger("remory.wellknown")
router = APIRouter(prefix="/.well-known", tags=["well-known"])


@router.get("/apple-app-site-association")
def apple_app_site_association():
    """iOS Associated Domains(webcredentials) 연결 파일."""
    if not settings.ios_app_ids:
        logger.warning("IOS_APP_IDS 미설정: iOS 패스키가 동작하지 않습니다.")
    # 확장자 없는 경로 + application/json 으로 서빙(Apple 요구사항).
    return JSONResponse(
        {"webcredentials": {"apps": settings.ios_app_ids}},
        media_type="application/json",
    )


@router.get("/assetlinks.json")
def assetlinks():
    """Android Digital Asset Links 연결 파일."""
    if not (settings.android_package_name and settings.android_sha256_fingerprints):
        logger.warning("ANDROID_* 미설정: Android 패스키가 동작하지 않습니다.")
    return JSONResponse(
        [
            {
                # 예전 Play 서비스(FIDO2) 는 handle_all_urls 도 있어야 도메인을
                # 확인해 준다. 없으면 'RP ID cannot be validated' 로 등록이 막힌다.
                "relation": [
                    "delegate_permission/common.handle_all_urls",
                    "delegate_permission/common.get_login_creds",
                ],
                "target": {
                    "namespace": "android_app",
                    "package_name": settings.android_package_name,
                    "sha256_cert_fingerprints": settings.android_sha256_fingerprints,
                },
            }
        ],
        media_type="application/json",
    )
