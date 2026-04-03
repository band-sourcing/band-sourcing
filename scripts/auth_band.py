#!/usr/bin/env python3
"""
Band OAuth2 토큰 발급 스크립트.

사용법:
  1) .env 파일에 BAND_CLIENT_ID, BAND_CLIENT_SECRET 설정
  2) python scripts/auth_band.py
  3) 브라우저에서 Band 로그인 + 권한 승인
  4) 토큰이 .env 파일에 자동 저장됨
"""

import os
import sys
import base64
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

import requests
from dotenv import load_dotenv

CALLBACK_PORT = 7777
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"

AUTH_URL = "https://auth.band.us/oauth2/authorize"
TOKEN_URL = "https://auth.band.us/oauth2/token"

# 콜백으로 받은 code를 저장할 컨테이너
_auth_result = {"code": None, "error": None}


class CallbackHandler(BaseHTTPRequestHandler):
    """로컬 HTTP 서버 — Band 리다이렉트 콜백 수신."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _auth_result["code"] = params["code"][0]
            self._respond("Band 인증 성공! 이 창을 닫아도 됩니다.")
        else:
            error = params.get("error", ["unknown"])[0]
            _auth_result["error"] = error
            self._respond(f"Band 인증 실패: {error}")

    def _respond(self, message: str):
        html = f"""<html><body style="font-family:sans-serif;text-align:center;padding-top:80px;">
        <h2>{message}</h2></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        """서버 로그 억제."""
        pass


def start_callback_server() -> HTTPServer:
    server = HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server


def build_auth_url(client_id: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_token(client_id: str, client_secret: str, code: str) -> dict:
    """authorization_code → access_token 교환."""
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    resp = requests.get(
        TOKEN_URL,
        params={
            "grant_type": "authorization_code",
            "code": code,
        },
        headers={
            "Authorization": f"Basic {credentials}",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"토큰 교환 실패: {resp.status_code}")
        print(resp.text)
        sys.exit(1)

    return resp.json()


def save_token_to_env(access_token: str):
    """
    .env 파일에 BAND_ACCESS_TOKEN 저장.
    기존 값이 있으면 교체, 없으면 추가.
    """
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)

    lines = []
    found = False

    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.startswith("BAND_ACCESS_TOKEN="):
            new_lines.append(f"BAND_ACCESS_TOKEN={access_token}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"BAND_ACCESS_TOKEN={access_token}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"토큰 저장 완료: {env_path}")


def main():
    load_dotenv()

    client_id = os.getenv("BAND_CLIENT_ID", "").strip()
    client_secret = os.getenv("BAND_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("오류: .env 파일에 BAND_CLIENT_ID, BAND_CLIENT_SECRET을 설정하세요.")
        sys.exit(1)

    print(f"콜백 서버 시작: http://localhost:{CALLBACK_PORT}/callback")
    server = start_callback_server()

    auth_url = build_auth_url(client_id)
    print(f"\n브라우저에서 Band 로그인 페이지를 엽니다...")
    print(f"자동으로 열리지 않으면 아래 URL을 직접 열어주세요:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("Band 로그인 + 권한 승인 대기 중...")
    server.server_close()

    if _auth_result["error"]:
        print(f"인증 실패: {_auth_result['error']}")
        sys.exit(1)

    code = _auth_result["code"]
    if not code:
        print("인증 코드를 받지 못했습니다.")
        sys.exit(1)

    print(f"인증 코드 수신 완료. 토큰 교환 중...")

    token_data = exchange_token(client_id, client_secret, code)
    access_token = token_data.get("access_token")

    if not access_token:
        print(f"토큰 응답에 access_token이 없음: {token_data}")
        sys.exit(1)

    print(f"access_token 발급 성공!")

    if "refresh_token" in token_data:
        print(f"refresh_token: {token_data['refresh_token'][:20]}...")
    if "expires_in" in token_data:
        days = token_data["expires_in"] // 86400
        print(f"만료: {days}일 후")

    save_token_to_env(access_token)
    print("\n완료! 이제 main.py를 실행할 수 있습니다.")


if __name__ == "__main__":
    main()
