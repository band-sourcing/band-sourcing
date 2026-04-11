# save as: get_band_cookies.py
import json, time
from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
browser = pw.chromium.launch(headless=False)  # 브라우저 보임
context = browser.new_context(viewport={"width":1280,"height":800})
page = context.new_page()

page.goto("https://auth.band.us/login_page")
print("=" * 50)
print("브라우저에서 네이버로 직접 로그인하세요!")
print("로그인 완료 후 밴드 피드가 보이면 엔터를 누르세요")
print("=" * 50)
input()

cookies = context.cookies()
with open("band_session.json", "w", encoding="utf-8") as f:
    json.dump(cookies, f, ensure_ascii=False, indent=2)

print(f"쿠키 저장 완료! band_session.json ({len(cookies)}개)")
browser.close()
pw.stop()
