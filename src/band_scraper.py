"""
Band 게시글 웹 스크래퍼 (Playwright 기반).

band_fetcher.py의 드롭인 교체 모듈.
Band Open API 승인 없이 웹 크롤링으로 동일한 데이터를 수집한다.

인터페이스:
  - get_band_keys(target_names) -> dict[str, str]
  - fetch_all_posts(band_key) -> list[dict]
  - close()

반환 포스트 형식 (main.py 호환):
  {
    "post_key": str,
    "created_at": int (ms timestamp),
    "content": str,
    "photos": [{"url": str}],
  }
"""

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext

logger = logging.getLogger(__name__)

BAND_HOME = "https://band.us"
BAND_LOGIN = "https://auth.band.us/login_page"


class BandScraper:
    """Playwright 기반 밴드 스크래퍼. BandFetcher와 동일 인터페이스."""

    # execution context 파괴 시 retry 설정
    MAX_EVAL_RETRIES = 3
    EVAL_RETRY_DELAY = 2.0

    def __init__(
        self,
        naver_id: str,
        naver_pw: str,
        cutoff_date: str,
        headless: bool = True,
        session_path: str = "data/band_session.json",
    ):
        self.naver_id = naver_id
        self.naver_pw = naver_pw
        self.cutoff = datetime.strptime(cutoff_date, "%Y-%m-%d")
        self.headless = headless
        self.session_path = session_path

        self._pw = sync_playwright().start()
        self._browser: Browser = self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._logged_in = False

    # ── 안전한 JS 실행 (context 파괴 대응) ──

    def _safe_evaluate(self, expression: str, retries: int = None):
        """
        page.evaluate()를 실행하되, execution context 파괴 시 재시도.
        밴드 SPA가 스크롤 중 네비게이션을 트리거하면 context가 파괴될 수 있다.
        """
        max_retries = retries or self.MAX_EVAL_RETRIES
        last_error = None

        for attempt in range(max_retries):
            try:
                return self._page.evaluate(expression)
            except Exception as e:
                err_msg = str(e).lower()
                if "execution context" in err_msg or "destroyed" in err_msg or "navigat" in err_msg:
                    last_error = e
                    logger.warning(
                        f"Execution context 파괴 감지 (시도 {attempt + 1}/{max_retries}): {e}"
                    )
                    self._wait_for_stable_context()
                else:
                    raise

        logger.error(f"_safe_evaluate 최종 실패 ({max_retries}회 재시도 후): {last_error}")
        raise last_error

    def _wait_for_stable_context(self):
        """
        네비게이션 완료 후 안정적인 context가 확보될 때까지 대기.
        밴드 SPA 내부 네비게이션(pushState) 또는 full navigation 모두 대응.
        """
        try:
            # 진행 중인 네비게이션 완료 대기
            self._page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

        time.sleep(self.EVAL_RETRY_DELAY)

        # context가 실제로 살아있는지 간단한 JS로 확인
        for probe in range(3):
            try:
                self._page.evaluate("1 + 1")
                return
            except Exception:
                time.sleep(1)

        logger.warning("context 안정화 실패 -> 현재 URL로 강제 reload")
        try:
            current_url = self._page.url
            self._page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
        except Exception as e:
            logger.error(f"reload 실패: {e}")

    def _safe_goto(self, url: str, timeout: int = 15000):
        """
        page.goto()를 실행하되, context 파괴 시 재시도.
        상세 페이지 방문(2-pass/3-pass)에서 사용.
        """
        for attempt in range(self.MAX_EVAL_RETRIES):
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                time.sleep(1.5)
                return
            except Exception as e:
                err_msg = str(e).lower()
                if "execution context" in err_msg or "destroyed" in err_msg:
                    logger.warning(
                        f"goto context 파괴 (시도 {attempt + 1}): {url}"
                    )
                    self._wait_for_stable_context()
                else:
                    raise
        raise Exception(f"_safe_goto 최종 실패: {url}")

    # ── 세션 관리 ──

    def _save_session(self):
        """쿠키를 파일에 저장하여 재로그인 방지."""
        if self._context:
            cookies = self._context.cookies()
            os.makedirs(os.path.dirname(self.session_path), exist_ok=True)
            with open(self.session_path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False)
            logger.info(f"세션 저장 완료: {self.session_path}")

    def _load_session(self) -> bool:
        """저장된 세션 쿠키 로드. 성공 시 True."""
        if not os.path.exists(self.session_path):
            return False
        try:
            with open(self.session_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            self._context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            self._context.add_cookies(cookies)
            self._page = self._context.new_page()
            logger.info("저장된 세션 로드 완료")
            return True
        except Exception as e:
            logger.warning(f"세션 로드 실패: {e}")
            return False

    def _is_session_valid(self) -> bool:
        """현재 세션이 유효한지 확인."""
        try:
            self._page.goto(f"{BAND_HOME}/feed", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
            url = self._page.url
            if "auth.band.us" in url or "login" in url:
                return False
            return True
        except Exception:
            return False

    # ── 로그인 ──

    def _login_naver(self):
        """네이버 계정으로 밴드 로그인."""
        logger.info("네이버 계정으로 밴드 로그인 시작...")

        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        self._page = self._context.new_page()

        self._page.goto(BAND_LOGIN, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        naver_btn = self._page.locator('a.-naver, button.-naver, [class*="naver"]').first
        if naver_btn.count() == 0:
            naver_btn = self._page.locator('a[href*="naver"], button:has-text("네이버")')
        naver_btn.first.click()
        time.sleep(5)

        current_url = self._page.url
        if "nid.naver.com" in current_url:
            id_input = self._page.locator('input#id, input[name="id"]').first
            id_input.fill("")
            self._page.evaluate(
                f'document.querySelector("input#id, input[name=\\"id\\"]").value = "{self.naver_id}"'
            )
            id_input.dispatch_event("input")
            time.sleep(0.5)

            pw_input = self._page.locator('input#pw, input[name="pw"]').first
            pw_input.fill("")
            self._page.evaluate(
                f'document.querySelector("input#pw, input[name=\\"pw\\"]").value = "{self.naver_pw}"'
            )
            pw_input.dispatch_event("input")
            time.sleep(0.5)

            login_btn = self._page.locator(
                'button#log\\.login, button[type="submit"], input[type="submit"]'
            ).first
            login_btn.click()
            time.sleep(10)

        logger.info("로그인 대기 중... (최대 120초)")
        for i in range(120):
            current = self._page.url
            if i % 10 == 0:
                logger.info(f"  대기 {i}초... URL: {current}")
            if "band.us" in current and "auth" not in current and "login" not in current:
                logger.info(f"  로그인 성공! URL: {current}")
                break
            time.sleep(1)
        else:
            final_url = self._page.url
            try:
                self._page.screenshot(path="data/login_fail.png")
                logger.info("로그인 실패 스크린샷: data/login_fail.png")
            except:
                pass
            raise Exception(
                f"밴드 로그인 실패 (120초 타임아웃) "
                f"최종 URL: {final_url}"
            )

        self._logged_in = True
        self._save_session()
        logger.info("밴드 로그인 성공!")

    def ensure_logged_in(self):
        """로그인 상태 보장."""
        if self._logged_in:
            return

        if self._load_session() and self._is_session_valid():
            self._logged_in = True
            logger.info("저장된 세션으로 로그인 확인됨")
            return

        self._login_naver()

    # ── 밴드 검색 (get_band_keys 호환) ──

    def get_band_keys(self, target_names: list[str]) -> dict[str, str]:
        self.ensure_logged_in()

        result = {}
        self._page.goto(f"{BAND_HOME}/feed", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        for band_name in target_names:
            try:
                band_key = self._find_band_key(band_name)
                if band_key:
                    result[band_name] = band_key
                    logger.info(f"밴드 발견: {band_name} -> {band_key}")
                else:
                    logger.warning(f"밴드를 찾을 수 없음: {band_name}")
            except Exception as e:
                logger.error(f"밴드 검색 실패 ({band_name}): {e}")

        return result

    def _find_band_key(self, band_name: str) -> str | None:
        self._page.goto(f"{BAND_HOME}/feed", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        band_links = self._page.locator('a[href*="/band/"]')
        count = band_links.count()

        for i in range(count):
            link = band_links.nth(i)
            text = link.inner_text().strip()
            href = link.get_attribute("href") or ""

            if band_name in text:
                m = re.search(r'/band/(\d+)', href)
                if m:
                    return m.group(1)

        logger.info(f"사이드바에서 {band_name} 못 찾음 -> 직접 밴드 페이지 탐색")

        self._page.goto(f"{BAND_HOME}/my_bands", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        band_links = self._page.locator('a[href*="/band/"]')
        count = band_links.count()

        for i in range(count):
            link = band_links.nth(i)
            text = link.inner_text().strip()
            href = link.get_attribute("href") or ""

            if band_name in text:
                m = re.search(r'/band/(\d+)', href)
                if m:
                    return m.group(1)

        return None

    # ── 게시글 수집 (fetch_all_posts 호환) ──

    def fetch_all_posts(self, band_key: str) -> list[dict]:
        """
        밴드의 게시글을 스크롤하면서 수집.
        cutoff_date 이전 게시글이 나오면 중단.
        2-pass: 가격코드 누락된 게시글은 상세 페이지에서 보충.
        """
        self.ensure_logged_in()

        band_url = f"{BAND_HOME}/band/{band_key}"
        self._page.goto(band_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        all_posts = []
        seen_keys = set()
        no_new_count = 0
        max_scroll_attempts = 100

        context_error_count = 0
        MAX_CONTEXT_ERRORS = 5

        for scroll_attempt in range(max_scroll_attempts):
            # context 파괴 후 locator 재취득 필요
            try:
                post_elements = self._page.locator('article._postMainWrap')
                current_count = post_elements.count()
            except Exception as e:
                err_msg = str(e).lower()
                if "execution context" in err_msg or "destroyed" in err_msg:
                    context_error_count += 1
                    logger.warning(
                        f"스크롤 중 context 파괴 (#{context_error_count}) -> 복구 시도"
                    )
                    if context_error_count > MAX_CONTEXT_ERRORS:
                        logger.error("context 파괴 횟수 초과 -> 수집 중단")
                        break
                    self._wait_for_stable_context()
                    # 밴드 피드가 다른 페이지로 이동했을 수 있으므로 URL 확인
                    if f"/band/{band_key}" not in self._page.url:
                        logger.info("피드 이탈 감지 -> 밴드 피드로 복귀")
                        self._page.goto(band_url, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(3)
                    continue
                raise

            new_found = False
            stop_scrolling = False

            for i in range(current_count):
                try:
                    post_el = post_elements.nth(i)
                    post_data = self._parse_post_element(post_el, band_key)

                    if not post_data:
                        continue

                    if post_data["post_key"] in seen_keys:
                        continue

                    created = datetime.fromtimestamp(post_data["created_at"] / 1000)
                    if created < self.cutoff:
                        stop_scrolling = True
                        break

                    seen_keys.add(post_data["post_key"])
                    all_posts.append(post_data)
                    new_found = True

                except Exception as e:
                    err_msg = str(e).lower()
                    if "execution context" in err_msg or "destroyed" in err_msg:
                        logger.warning(f"게시글 파싱 중 context 파괴 (index {i}) -> 이번 스크롤 스킵")
                        self._wait_for_stable_context()
                        break
                    logger.debug(f"게시글 파싱 실패 (index {i}): {e}")
                    continue

            if stop_scrolling:
                logger.info(f"cutoff 날짜 도달 (scroll #{scroll_attempt})")
                break

            if not new_found:
                no_new_count += 1
                if no_new_count >= 5:
                    logger.info("더 이상 새 게시글 없음 -> 스크롤 중단")
                    break
            else:
                no_new_count = 0

            # 안전한 스크롤 실행
            try:
                self._safe_evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception as e:
                logger.warning(f"스크롤 실패 -> 밴드 피드 복귀: {e}")
                self._page.goto(band_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
            time.sleep(2)

        logger.info(f"밴드 {band_key}: 1차 수집 {len(all_posts)}개")

        # ── 2-pass: 가격코드 없는 게시글 상세 페이지 방문 ──
        PRICE_RE = re.compile(r'\d{3}\s*\([A-Za-z][A-Za-z0-9]*\)')
        need_detail = [p for p in all_posts if p["content"] and not PRICE_RE.search(p["content"])]
        logger.info(f"  가격코드 누락 {len(need_detail)}개 -> 상세 페이지 보충 시작")

        for idx, post in enumerate(need_detail):
            post_url = post.get("_detail_url", "")
            if not post_url:
                continue
            try:
                self._safe_goto(post_url)

                detail_selectors = [
                    'p.txtBody',
                    '.postText',
                    '._postText',
                    '[class*="postText"]',
                    '[class*="txtBody"]',
                    '.dPostBody',
                    '._postBody',
                ]

                for sel in detail_selectors:
                    try:
                        text_el = self._page.locator(sel).first
                        if text_el.count() > 0:
                            text = text_el.inner_text().strip()
                            if text and len(text) > 3 and PRICE_RE.search(text):
                                post["content"] = text
                                logger.debug(f"  [{idx}] 상세 보충 성공: {len(text)}자")
                                break
                    except Exception as e:
                        err_msg = str(e).lower()
                        if "execution context" in err_msg or "destroyed" in err_msg:
                            self._wait_for_stable_context()
                            break
                        continue

            except Exception as e:
                logger.debug(f"  [{idx}] 상세 진입 실패: {e}")

        filled = sum(1 for p in need_detail if PRICE_RE.search(p.get("content", "")))
        logger.info(f"  상세 보충 완료: {filled}/{len(need_detail)}개 성공")

        # ── 3-pass: 이미지 4장 이하 게시글 → 상세 페이지에서 이미지 재추출 ──
        # 밴드 피드 UI가 4장까지만 표시하므로 상세 페이지에서 전체 이미지를 가져온다
        MAX_FEED_IMAGES = 4
        need_images = [p for p in all_posts if len(p.get("photos", [])) <= MAX_FEED_IMAGES and p.get("photos")]
        logger.info(f"  이미지 보충 대상 {len(need_images)}개 (≤{MAX_FEED_IMAGES}장)")

        img_supplemented = 0
        for idx, post in enumerate(need_images):
            post_url = post.get("_detail_url", "")
            if not post_url:
                continue
            old_count = len(post["photos"])
            try:
                self._safe_goto(post_url)

                # 상세 페이지의 게시글 컨테이너에서 이미지 추출
                detail_el = self._page.locator('article._postMainWrap').first
                if detail_el.count() == 0:
                    detail_el = self._page.locator('.dPostBody, ._postBody, .postBody').first
                if detail_el.count() > 0:
                    detail_photos = self._extract_photos(detail_el)
                    if len(detail_photos) > old_count:
                        post["photos"] = detail_photos
                        img_supplemented += 1
                        logger.debug(f"  [{idx}] 이미지 보충: {old_count}→{len(detail_photos)}장")
            except Exception as e:
                logger.debug(f"  [{idx}] 이미지 상세 진입 실패: {e}")

        logger.info(f"  이미지 보충 완료: {img_supplemented}/{len(need_images)}개 보충됨")

        logger.info(f"밴드 {band_key}: 총 {len(all_posts)}개 게시글 수집")
        return all_posts

    def _parse_post_element(self, el, band_key: str) -> dict | None:
        """단일 게시글 DOM 요소에서 데이터 추출."""
        try:
            post_key = self._extract_post_key(el)
            if not post_key:
                return None

            content = self._extract_content(el)
            if not content:
                return None

            created_at = self._extract_timestamp(el)
            if not created_at:
                return None

            photos = self._extract_photos(el)

            detail_url = self._extract_post_url(el)

            return {
                "post_key": f"{band_key}_{post_key}",
                "created_at": created_at,
                "content": content,
                "photos": photos,
                "_detail_url": detail_url,
            }

        except Exception as e:
            logger.debug(f"게시글 파싱 에러: {e}")
            return None

    def _extract_post_key(self, el) -> str | None:
        """게시글 고유 키 추출."""
        for attr in ["data-post-id", "data-postid", "data-post_key"]:
            val = el.get_attribute(attr)
            if val:
                return val

        link = el.locator('a[href*="/post/"]').first
        if link.count() > 0:
            href = link.get_attribute("href") or ""
            m = re.search(r'/post/([^/?]+)', href)
            if m:
                return m.group(1)

        content = el.inner_text()[:200]
        if content.strip():
            return hashlib.md5(content.encode()).hexdigest()[:16]

        return None

    def _extract_content(self, el) -> str:
        """게시글 본문 텍스트 추출 (피드 목록에서만 - 스크롤 안전)."""
        selectors = [
            'p.txtBody',
            '.postText',
            '._postText',
            '[class*="postText"]',
            '[class*="txtBody"]',
        ]

        for sel in selectors:
            text_el = el.locator(sel).first
            if text_el.count() > 0:
                text = text_el.inner_text().strip()
                if text and len(text) > 3:
                    return text

        return ""

    def _extract_post_url(self, el) -> str:
        """게시글 상세 페이지 URL 추출."""
        link = el.locator('a[href*="/post/"]').first
        if link.count() == 0:
            return ""
        href = link.get_attribute("href") or ""
        if href.startswith("/"):
            return f"https://band.us{href}"
        if href.startswith("http"):
            return href
        return ""

    def _extract_timestamp(self, el) -> int | None:
        """게시글 작성 시간을 ms timestamp로 추출."""
        time_el = el.locator("time").first
        if time_el.count() > 0:
            dt_attr = time_el.get_attribute("datetime")
            if dt_attr:
                try:
                    dt = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
                    return int(dt.timestamp() * 1000)
                except Exception:
                    pass

            date_text = time_el.inner_text().strip()
            ts = self._parse_date_text(date_text)
            if ts:
                return ts

        for attr in ["data-created-at", "data-timestamp", "data-time"]:
            val = el.get_attribute(attr)
            if val and val.isdigit():
                ts = int(val)
                if ts > 1e12:
                    return ts
                return ts * 1000

        return None

    def _parse_date_text(self, text: str) -> int | None:
        """한국어 날짜 텍스트를 ms timestamp로 변환."""
        m = re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', text)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                return int(dt.timestamp() * 1000)
            except Exception:
                pass

        m = re.search(r'(\d{1,2})월\s*(\d{1,2})일', text)
        if m:
            try:
                dt = datetime(datetime.now().year, int(m.group(1)), int(m.group(2)))
                return int(dt.timestamp() * 1000)
            except Exception:
                pass

        m = re.search(r'(\d+)\s*시간\s*전', text)
        if m:
            hours = int(m.group(1))
            return int((time.time() - hours * 3600) * 1000)

        m = re.search(r'(\d+)\s*분\s*전', text)
        if m:
            mins = int(m.group(1))
            return int((time.time() - mins * 60) * 1000)

        m = re.search(r'(\d+)\s*일\s*전', text)
        if m:
            days = int(m.group(1))
            return int((time.time() - days * 86400) * 1000)

        if "어제" in text:
            return int((time.time() - 86400) * 1000)

        if "방금" in text:
            return int(time.time() * 1000)

        return None

    def _extract_photos(self, el) -> list[dict]:
        """게시글 이미지 URL 추출."""
        photos = []
        seen_urls = set()

        SKIP_PATTERNS = [
            "profile", "avatar", "icon", "emoji",
            "sticker", "emoticon", "gif_origin",
            "logo", "1x1",
            # 밴드 공통 이미지 (프로필/배너)
            "a_h9hUd018svc19uvfzsdn00w9_4k8958",
            "_5ksoqj",
        ]

        # 밴드 하단 고정 이미지 6개 (정확한 URL 매칭)
        FOOTER_EXACT = {
            "3_c94Ud018svcuglxur2ti9xu_fwc5at",
            "3_b94Ud018svct4ncqwo9v77y_fwc5at",
            "3_a94Ud018svcafq130uomry2_fwc5at",
            "3_894Ud018svc1hofcwsdwnyjf_fwc5at",
            "3_794Ud018svc1jlqptz5ydktb_fwc5at",
            "3_694Ud018svc1k59mta8uohbe_fwc5at",
        }

        def _should_skip(url: str) -> bool:
            low = url.lower()
            if any(skip in low for skip in SKIP_PATTERNS):
                return True
            if any(fid in url for fid in FOOTER_EXACT):
                return True
            return False

        img_elements = el.locator('img._image')
        count = img_elements.count()

        for i in range(count):
            try:
                img = img_elements.nth(i)
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""

                if not src or not src.startswith("http"):
                    continue
                if _should_skip(src):
                    continue
                if src in seen_urls:
                    continue

                seen_urls.add(src)
                clean_url = self._get_full_res_url(src)
                photos.append({"url": clean_url})
            except Exception:
                continue

        if not photos:
            all_imgs = el.locator('img')
            count = all_imgs.count()
            for i in range(count):
                try:
                    img = all_imgs.nth(i)
                    src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                    alt = img.get_attribute("alt") or ""

                    if not src or not src.startswith("http"):
                        continue
                    if _should_skip(src):
                        continue
                    if "phinf" not in src and "사용자" not in alt:
                        continue
                    if src in seen_urls:
                        continue

                    seen_urls.add(src)
                    clean_url = self._get_full_res_url(src)
                    photos.append({"url": clean_url})
                except Exception:
                    continue

        return photos

    @staticmethod
    def _get_full_res_url(url: str) -> str:
        """밴드 이미지 URL을 최대 해상도로 변환."""
        url = re.sub(r'[?&]type=[^&]+', '', url)
        url = re.sub(r'/\d+x\d+/', '/', url)
        url = re.sub(r'[?&]w=\d+', '', url)
        url = re.sub(r'[?&]h=\d+', '', url)
        url = re.sub(r'\?&', '?', url)
        url = re.sub(r'\?$', '', url)
        return url

    # ── 정리 ──

    def close(self):
        """브라우저 종료."""
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            logger.debug(f"close 에러 (무시): {e}")


if __name__ == "__main__":
    print("band_scraper.py 로드 성공!")
    print("실제 테스트: python -c 'from src.band_scraper import BandScraper; print(\"OK\")'")
