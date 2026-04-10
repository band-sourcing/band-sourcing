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

        # Band 로그인 페이지
        self._page.goto(BAND_LOGIN, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        # 네이버 로그인 버튼 클릭
        naver_btn = self._page.locator('a.-naver, button.-naver, [class*="naver"]').first
        if naver_btn.count() == 0:
            naver_btn = self._page.locator('a[href*="naver"], button:has-text("네이버")')
        naver_btn.first.click()
        time.sleep(5)

        # 네이버 로그인 폼
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

        # 로그인 후 Band 피드로 리다이렉트 대기
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
        """
        가입된 밴드 목록에서 target_names에 해당하는 밴드의 URL key를 추출.
        반환: {"잡화천국22": "band_url_key", ...}
        """
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
        """밴드 이름으로 검색하여 band_key(URL path)를 찾는다."""
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
        """
        self.ensure_logged_in()

        band_url = f"{BAND_HOME}/band/{band_key}"
        self._page.goto(band_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        all_posts = []
        seen_keys = set()
        no_new_count = 0
        max_scroll_attempts = 100

        for scroll_attempt in range(max_scroll_attempts):
            post_elements = self._page.locator(
                '[class*="postWrap"], [class*="post_wrap"], '
                '[data-viewname*="post"], article[class*="post"]'
            )
            current_count = post_elements.count()

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

            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

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

            return {
                "post_key": f"{band_key}_{post_key}",
                "created_at": created_at,
                "content": content,
                "photos": photos,
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
        """게시글 본문 텍스트 추출."""
        selectors = [
            '[class*="postText"]',
            '[class*="post_text"]',
            '[class*="postBody"]',
            '[class*="post_body"]',
            '[class*="txtBody"]',
            '[class*="txt_body"]',
            '.postContent',
            '.post_content',
            'p[class*="text"]',
        ]

        for sel in selectors:
            text_el = el.locator(sel).first
            if text_el.count() > 0:
                text = text_el.inner_text().strip()
                if text and len(text) > 5:
                    return text

        full_text = el.inner_text().strip()
        if full_text and len(full_text) > 10:
            return full_text

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

        for attr in ["data-created-at", "data-timestamp", "data-time"]:
            val = el.get_attribute(attr)
            if val and val.isdigit():
                ts = int(val)
                if ts > 1e12:
                    return ts
                return ts * 1000

        date_selectors = [
            '[class*="date"]',
            '[class*="time"]',
            '[class*="ago"]',
            'span[class*="post_time"]',
        ]
        for sel in date_selectors:
            date_el = el.locator(sel).first
            if date_el.count() > 0:
                date_text = date_el.inner_text().strip()
                ts = self._parse_date_text(date_text)
                if ts:
                    return ts

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

        return None

    def _extract_photos(self, el) -> list[dict]:
        """
        게시글 이미지 URL 추출 (강화 버전).

        밴드 웹은 이미지를 여러 방식으로 렌더링:
        1. <img src="..."> 또는 <img data-src="..."> (lazy load)
        2. <div style="background-image: url(...)"> (썸네일)
        3. <a> 또는 <div> 내 data-image-url 속성
        4. 이미지 래퍼 안의 숨겨진 원본 URL
        """
        photos = []
        seen_urls = set()

        SKIP_PATTERNS = [
            "profile", "avatar", "icon", "emoji", "static",
            "banner", "logo", "thumb_small", "1x1",
            "sticker", "emoticon", "gif_origin",
        ]

        def _should_skip(url: str) -> bool:
            low = url.lower()
            return any(skip in low for skip in SKIP_PATTERNS)

        def _is_valid_image_url(url: str) -> bool:
            if not url or len(url) < 10:
                return False
            if not url.startswith("http"):
                return False
            if _should_skip(url):
                return False
            return True

        def _add_photo(url: str):
            clean = self._get_full_res_url(url.strip())
            if clean and clean not in seen_urls and _is_valid_image_url(clean):
                seen_urls.add(clean)
                photos.append({"url": clean})

        # ── 방법 1: <img> 태그 (src / data-src / data-original) ──
        img_elements = el.locator("img")
        count = img_elements.count()
        for i in range(count):
            try:
                img = img_elements.nth(i)
                for attr in ["src", "data-src", "data-original", "data-lazy-src"]:
                    src = img.get_attribute(attr) or ""
                    if src:
                        _add_photo(src)
                        break  # 하나의 img에서 하나만
            except Exception:
                continue

        # ── 방법 2: background-image CSS (밴드 썸네일 패턴) ──
        bg_selectors = [
            '[class*="photo"] [style*="background"]',
            '[class*="image"] [style*="background"]',
            '[class*="thumb"] [style*="background"]',
            '[class*="img"] [style*="background"]',
            '[style*="background-image"]',
        ]
        for sel in bg_selectors:
            try:
                bg_els = el.locator(sel)
                bg_count = bg_els.count()
                for i in range(bg_count):
                    style = bg_els.nth(i).get_attribute("style") or ""
                    m = re.search(r'background-image:\s*url\(["\']?([^"\')\s]+)["\']?\)', style)
                    if m:
                        _add_photo(m.group(1))
            except Exception:
                continue

        # ── 방법 3: data-image-url / data-photo-url 속성 ──
        data_attr_selectors = [
            '[data-image-url]',
            '[data-photo-url]',
            '[data-original-url]',
            '[data-src-url]',
        ]
        for sel in data_attr_selectors:
            try:
                data_els = el.locator(sel)
                data_count = data_els.count()
                for i in range(data_count):
                    node = data_els.nth(i)
                    for attr in ["data-image-url", "data-photo-url", "data-original-url", "data-src-url"]:
                        val = node.get_attribute(attr) or ""
                        if val:
                            _add_photo(val)
            except Exception:
                continue

        # ── 방법 4: 이미지 갤러리 컨테이너 내부 <a> 링크 ──
        gallery_selectors = [
            '[class*="photoList"] a',
            '[class*="photo_list"] a',
            '[class*="imageList"] a',
            '[class*="image_list"] a',
            '[class*="photoGrid"] a',
            '[class*="photo_grid"] a',
            '[class*="postPhoto"] a',
            '[class*="post_photo"] a',
        ]
        for sel in gallery_selectors:
            try:
                links = el.locator(sel)
                link_count = links.count()
                for i in range(link_count):
                    href = links.nth(i).get_attribute("href") or ""
                    if href and ("phinf" in href or "dthumb" in href or href.endswith((".jpg", ".jpeg", ".png", ".webp"))):
                        _add_photo(href)
            except Exception:
                continue

        # ── 방법 5: JavaScript로 DOM에서 직접 이미지 URL 추출 ──
        if not photos:
            try:
                js_urls = el.evaluate("""(el) => {
                    const urls = [];

                    // img 태그
                    el.querySelectorAll('img').forEach(img => {
                        const src = img.src || img.dataset.src || img.dataset.original || '';
                        if (src && src.startsWith('http')) urls.push(src);
                    });

                    // background-image
                    el.querySelectorAll('*').forEach(node => {
                        const bg = window.getComputedStyle(node).backgroundImage;
                        if (bg && bg !== 'none') {
                            const m = bg.match(/url\\(["']?([^"')]+)["']?\\)/);
                            if (m && m[1].startsWith('http')) urls.push(m[1]);
                        }
                    });

                    // data 속성
                    el.querySelectorAll('[data-image-url], [data-photo-url], [data-original-url]').forEach(node => {
                        const val = node.dataset.imageUrl || node.dataset.photoUrl || node.dataset.originalUrl || '';
                        if (val && val.startsWith('http')) urls.push(val);
                    });

                    return [...new Set(urls)];
                }""")
                for url in (js_urls or []):
                    _add_photo(url)
            except Exception as e:
                logger.debug(f"JS 이미지 추출 실패: {e}")

        if photos:
            logger.debug(f"이미지 {len(photos)}개 추출 완료")
        else:
            logger.debug("이미지 추출 실패 (0개)")

        return photos

    @staticmethod
    def _get_full_res_url(url: str) -> str:
        """밴드 이미지 URL을 최대 해상도로 변환."""
        # /xx_yy/ 사이즈 파라미터 제거
        url = re.sub(r'/\d+x\d+/', '/', url)
        # type=optimize 등 파라미터 제거하되 원본 URL 유지
        url = re.sub(r'[?&]type=[^&]+', '', url)
        # 밴드 CDN 리사이즈 파라미터 제거
        url = re.sub(r'[?&]w=\d+', '', url)
        url = re.sub(r'[?&]h=\d+', '', url)
        # 남은 ? 또는 & 정리
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
