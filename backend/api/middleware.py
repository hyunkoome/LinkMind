"""
인증 미들웨어 (2026-06-03 단계 A).

매 요청마다 httpOnly 쿠키의 JWT 를 디코드해 `request.state.auth` 에 payload 를 저장한다.
(없거나 만료면 None.) 인증 *강제* 는 여기서 하지 않는다 — 라우터의 `Depends(get_current_user)`
가 401 을 던진다. 미들웨어는 "토큰이 있으면 누구인지/어떤 space 인지" 만 채운다.

이렇게 분리한 이유:
  - 공개 경로(/auth/login, /health, /docs)는 토큰 없이 통과해야 함.
  - get_session(connection.py) 이 request.state.auth['space'] 를 읽어 Postgres RLS 변수
    (SET LOCAL app.current_space_id) 를 설정 → 라우터 본문 코드는 안 바뀜.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from backend.auth.security import decode_access_token
from backend.config import get_settings


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._cookie_name = get_settings().linkmind_cookie_name

    async def dispatch(self, request: Request, call_next):
        # 기본값 — 토큰 없거나 무효면 None (deps 가 401 판단).
        request.state.auth = None
        token = request.cookies.get(self._cookie_name)
        # 개발/외부 client 편의: Authorization: Bearer <token> 도 허용 (쿠키 우선).
        if not token:
            authz = request.headers.get("authorization", "")
            if authz.lower().startswith("bearer "):
                token = authz[7:].strip()
        if token:
            payload = decode_access_token(token)
            if payload:
                request.state.auth = payload
        return await call_next(request)
