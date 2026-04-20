import json, os, time

now = time.time()
session_ttl = now + 86400  # 세션 쿠키는 24시간으로 명시 (서버 거부 방지)

cookies = [
    {"name":"ai","value":"8679e7d,19db3b79ddf","domain":".band.us","path":"/","expires":-1,"httpOnly":False,"secure":False,"sameSite":"Lax"},
    {"name":"as","value":'"774d68c7:gVrfvmgp8VYy7Uo8jXkiSQQ7BH8CIyTabn/qSPctFTg="',"domain":".band.us","path":"/","expires":-1,"httpOnly":True,"secure":False,"sameSite":"Lax"},
    {"name":"band_session","value":"ZQIAAEbKrs9NtcxPfjFQJL4Qak-ZDJSC99ikQoB8r8Vu5w2xRTb9MynLak99dCLkhzp6mBb9aBmgppqxcgadzPLSaanVlhF8eU4x-lxoy9kbhMOk","domain":".band.us","path":"/","expires":-1,"httpOnly":False,"secure":False,"sameSite":"Lax"},
    {"name":"BBC","value":"df94ab81-c0e0-4c87-88e7-83600622b1df","domain":".band.us","path":"/","expires":1811072719.136,"httpOnly":False,"secure":False,"sameSite":"Lax"},
    {"name":"di","value":"web-AAAAABwtSwHeaQESCvrAZ047Juqhcj6cB1VJRks_NcR05U-j6Z1n8y4cySLC_2RxER143s","domain":".band.us","path":"/","expires":1811072726.567,"httpOnly":False,"secure":False,"sameSite":"Lax"},
    {"name":"JSESSIONID","value":"272E4630F7FF6C481036C4BB066E6E27","domain":"www.band.us","path":"/","expires":-1,"httpOnly":True,"secure":False,"sameSite":"Lax"},
    {"name":"language","value":"ko","domain":".band.us","path":"/","expires":1811305980.817,"httpOnly":False,"secure":False,"sameSite":"Lax"},
    {"name":"NAC","value":"NeCbDAirHpDrA","domain":".naver.com","path":"/","expires":1811035279.497,"httpOnly":False,"secure":True,"sameSite":"None"},
    {"name":"rt","value":'"ZQIAABEG9OmCBPihT45OrXJb3enPOlpDFhG5lCyfChxFrc2MUDKnClx-RSusCeRe3UXHY9F9cEs6Zw6MuD5-f1SoVHAza3bSq7oiIsU-7K_yiMCa,s"',"domain":".auth.band.us","path":"/","expires":-1,"httpOnly":True,"secure":False,"sameSite":"Lax"},
    {"name":"secretKey","value":'"UCxR4mlbl7wcdK5oE2PYZBzGD/mg+xqKkv+LIw7leDM="',"domain":".band.us","path":"/s/login/getKey","expires":-1,"httpOnly":True,"secure":True,"sameSite":"Lax"},
    {"name":"SESSION","value":"bAo/AfeaY76rJPsDDBxcDGdzCQkcXTDve/bspgcs+8wnRMa0mZIn+JEZw0P2WpF7","domain":"auth.band.us","path":"/","expires":-1,"httpOnly":True,"secure":True,"sameSite":"Lax"},
]

with open("band_session.json", "w", encoding="utf-8") as f:
    json.dump(cookies, f, ensure_ascii=False, indent=2)

print(f"생성 완료: {os.path.abspath('band_session.json')}")
print(f"쿠키 수: {len(cookies)}개")
