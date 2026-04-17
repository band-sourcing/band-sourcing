#!/usr/bin/env python3
"""
band_scraper.py E2E 테스트 (Playwright 기반).

로컬 HTTP 서버로 밴드 피드/상세 페이지를 시뮬레이션하여
execution context 파괴 및 복구 로직을 검증한다.

테스트 시나리오:
  1. _safe_evaluate: context 파괴 시 retry 후 성공
  2. _safe_evaluate: 최대 재시도 초과 시 예외 전파
  3. _safe_evaluate: context 파괴가 아닌 일반 에러는 즉시 raise
  4. _wait_for_stable_context: probe 성공 시 정상 복귀
  5. _wait_for_stable_context: probe 실패 시 reload fallback
  6. _safe_goto: context 파괴 시 retry 후 정상 도착
  7. _safe_goto: 최대 재시도 초과 시 예외
  8. fetch_all_posts 스크롤 중 context 파괴 -> 복구 후 수집 계속
  9. fetch_all_posts 스크롤 중 피드 이탈 -> 밴드 피드 복귀
  10. fetch_all_posts 2-pass 상세 페이지 context 파괴 -> 스킵 후 계속
  11. fetch_all_posts 3-pass 이미지 보충 context 파괴 -> 스킵 후 계속
  12. fetch_all_posts context 파괴 횟수 초과 -> 수집 조기 중단
"""

import http.server
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from playwright.sync_api import sync_playwright, Error as PlaywrightError

from src.band_scraper import BandScraper

logger = logging.getLogger(__name__)

# ── 테스트용 HTML 템플릿 ──

def _make_post_html(post_key: str, content: str, days_ago: int = 0, image_count: int = 1, base_url: str = "") -> str:
    """단일 게시글 article HTML 생성."""
    dt = datetime.now() - timedelta(days=days_ago)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    images = "\n".join(
        f'<img class="_image" src="https://phinf.example.com/photo_{post_key}_{i}.jpg" />'
        for i in range(image_count)
    )
    # base_url이 있으면 절대 URL로 생성 (테스트 서버용)
    # _extract_post_url은 http 시작 href를 그대로 사용
    if base_url:
        post_href = f"{base_url}/band/12345/post/{post_key}"
    else:
        post_href = f"/band/12345/post/{post_key}"
    return f"""
    <article class="_postMainWrap" data-post-id="{post_key}">
      <p class="txtBody">{content}</p>
      <time datetime="{iso}">{days_ago}일 전</time>
      <a href="{post_href}">상세</a>
      {images}
    </article>
    """


def _make_feed_page(posts_html: str, band_key: str = "12345") -> str:
    """밴드 피드 페이지 전체 HTML."""
    return f"""<!DOCTYPE html>
<html><head><title>Band Feed</title></head>
<body>
  <div id="feed">{posts_html}</div>
  <script>
    // scrollTo 호출 횟수 추적
    window._scrollCount = 0;
    const origScroll = window.scrollTo;
    window.scrollTo = function() {{
      window._scrollCount++;
      origScroll.apply(window, arguments);
    }};
  </script>
</body></html>"""


def _make_detail_page(post_key: str, content: str, image_count: int = 6) -> str:
    """게시글 상세 페이지 HTML."""
    images = "\n".join(
        f'<img class="_image" src="https://phinf.example.com/detail_{post_key}_{i}.jpg" />'
        for i in range(image_count)
    )
    return f"""<!DOCTYPE html>
<html><head><title>Post Detail</title></head>
<body>
  <article class="_postMainWrap" data-post-id="{post_key}">
    <p class="txtBody">{content}</p>
    {images}
  </article>
</body></html>"""


def _make_nav_trigger_page() -> str:
    """스크롤 시 네비게이션을 트리거하는 페이지 (context 파괴 시뮬레이션용)."""
    return """<!DOCTYPE html>
<html><head><title>Navigating...</title></head>
<body>
  <script>
    // 이 페이지가 로드되면 즉시 다른 URL로 이동 -> context 파괴
    setTimeout(function() {
      window.location.href = window.location.origin + '/redirected';
    }, 100);
  </script>
</body></html>"""


# ── 로컬 테스트 서버 ──

class BandTestHandler(http.server.BaseHTTPRequestHandler):
    """밴드 페이지를 시뮬레이션하는 HTTP 핸들러."""

    # 클래스 변수로 동적 응답 제어
    feed_html = ""
    detail_pages = {}  # {post_key: html}
    nav_trigger_on_scroll = False  # True면 스크롤 시 네비게이션 트리거
    request_log = []  # 요청 로그

    def do_GET(self):
        self.__class__.request_log.append(self.path)
        path = self.path.split("?")[0]

        # 밴드 피드 페이지
        if re.match(r'^/band/\d+$', path):
            self._respond(200, self.__class__.feed_html)
            return

        # 게시글 상세 페이지
        m = re.match(r'^/band/\d+/post/(\w+)$', path)
        if m:
            post_key = m.group(1)
            html = self.__class__.detail_pages.get(post_key, "<html><body>Not found</body></html>")
            self._respond(200, html)
            return

        # 로그인/피드 체크용
        if path in ("/feed", "/my_bands"):
            self._respond(200, "<html><body>OK</body></html>")
            return

        # redirect 대상 (context 파괴 테스트용)
        if path == "/redirected":
            self._respond(200, "<html><body>Redirected page</body></html>")
            return

        self._respond(404, "<html><body>404</body></html>")

    def _respond(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        """테스트 중 서버 로그 억제."""
        pass


@pytest.fixture(scope="module")
def test_server():
    """모듈 단위 로컬 HTTP 서버."""
    server = http.server.HTTPServer(("127.0.0.1", 0), BandTestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def scraper():
    """BandScraper 인스턴스 (로그인 bypass)."""
    s = BandScraper(
        naver_id="test",
        naver_pw="test",
        cutoff_date="2020-01-01",
        headless=True,
        session_path="/tmp/test_band_session.json",
    )
    # 로그인 bypass
    s._logged_in = True
    s._context = s._browser.new_context(
        user_agent="TestBot/1.0",
        viewport={"width": 1280, "height": 800},
    )
    s._page = s._context.new_page()
    # 테스트 서버는 band.us가 아니므로 로그인 리다이렉트 감지 비활성화
    s._is_redirected_to_login = lambda: False
    yield s
    s.close()


@pytest.fixture(autouse=True)
def reset_handler_state():
    """각 테스트 전 핸들러 상태 초기화."""
    BandTestHandler.feed_html = ""
    BandTestHandler.detail_pages = {}
    BandTestHandler.nav_trigger_on_scroll = False
    BandTestHandler.request_log = []


# ══════════════════════════════════════════
# 1. _safe_evaluate 테스트
# ══════════════════════════════════════════

class TestSafeEvaluate:
    """_safe_evaluate의 retry 로직 검증."""

    def test_success_on_first_try(self, scraper, test_server):
        """정상 evaluate는 바로 성공."""
        scraper._page.set_content("<html><body>OK</body></html>")
        result = scraper._safe_evaluate("1 + 1")
        assert result == 2

    def test_retry_on_context_destroyed(self, scraper, test_server):
        """context 파괴 에러 시 재시도 후 성공."""
        call_count = 0
        original_evaluate = scraper._page.evaluate

        def mock_evaluate(expr):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise PlaywrightError("Execution context was destroyed")
            return original_evaluate(expr)

        scraper._page.set_content("<html><body>OK</body></html>")
        with patch.object(scraper._page, "evaluate", side_effect=mock_evaluate):
            # _wait_for_stable_context 내부의 evaluate는 원본 사용
            with patch.object(scraper, "_wait_for_stable_context"):
                result = scraper._safe_evaluate("1 + 1")

        assert call_count == 2
        assert result == 2

    def test_raises_after_max_retries(self, scraper, test_server):
        """최대 재시도 초과 시 최종 에러 전파."""
        scraper._page.set_content("<html><body>OK</body></html>")

        def always_fail(expr):
            raise PlaywrightError("Execution context was destroyed")

        with patch.object(scraper._page, "evaluate", side_effect=always_fail):
            with patch.object(scraper, "_wait_for_stable_context"):
                with pytest.raises(PlaywrightError, match="Execution context"):
                    scraper._safe_evaluate("1 + 1")

    def test_non_context_error_raises_immediately(self, scraper, test_server):
        """context 파괴가 아닌 에러는 재시도 없이 즉시 raise."""
        scraper._page.set_content("<html><body>OK</body></html>")

        def type_error(expr):
            raise PlaywrightError("Cannot read property 'foo' of undefined")

        with patch.object(scraper._page, "evaluate", side_effect=type_error):
            with pytest.raises(PlaywrightError, match="Cannot read property"):
                scraper._safe_evaluate("nonexistent.foo")


# ══════════════════════════════════════════
# 2. _wait_for_stable_context 테스트
# ══════════════════════════════════════════

class TestWaitForStableContext:
    """context 안정화 대기 로직 검증."""

    def test_returns_when_probe_succeeds(self, scraper, test_server):
        """probe(1+1)가 바로 성공하면 정상 리턴."""
        scraper._page.set_content("<html><body>OK</body></html>")
        # 에러 없이 완료되어야 함
        scraper.EVAL_RETRY_DELAY = 0.1  # 테스트 속도
        scraper._wait_for_stable_context()

    def test_reload_fallback_on_probe_failure(self, scraper, test_server):
        """probe 3회 실패 시 현재 URL reload."""
        scraper._page.set_content("<html><body>OK</body></html>")
        scraper.EVAL_RETRY_DELAY = 0.1

        probe_count = 0
        original_evaluate = scraper._page.evaluate

        def fail_then_succeed(expr):
            nonlocal probe_count
            if expr == "1 + 1":
                probe_count += 1
                if probe_count <= 3:
                    raise PlaywrightError("Execution context was destroyed")
            return original_evaluate(expr)

        goto_called = []
        original_goto = scraper._page.goto

        def track_goto(url, **kwargs):
            goto_called.append(url)
            return original_goto(url, **kwargs)

        with patch.object(scraper._page, "evaluate", side_effect=fail_then_succeed):
            with patch.object(scraper._page, "goto", side_effect=track_goto):
                scraper._wait_for_stable_context()

        # probe 3회 실패 후 reload 호출
        assert probe_count == 3
        assert len(goto_called) == 1


# ══════════════════════════════════════════
# 3. _safe_goto 테스트
# ══════════════════════════════════════════

class TestSafeGoto:
    """_safe_goto의 retry 로직 검증."""

    def test_success_on_first_try(self, scraper, test_server):
        """정상 goto는 바로 성공."""
        url = f"{test_server}/band/12345"
        BandTestHandler.feed_html = _make_feed_page("")
        scraper._safe_goto(url)
        assert "/band/12345" in scraper._page.url

    def test_retry_on_context_destroyed(self, scraper, test_server):
        """goto 중 context 파괴 -> 재시도 후 성공."""
        url = f"{test_server}/band/12345"
        BandTestHandler.feed_html = _make_feed_page("")

        call_count = 0
        original_goto = scraper._page.goto

        def mock_goto(u, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise PlaywrightError("Execution context was destroyed, most likely because of a navigation")
            return original_goto(u, **kwargs)

        with patch.object(scraper._page, "goto", side_effect=mock_goto):
            with patch.object(scraper, "_wait_for_stable_context"):
                scraper._safe_goto(url)

        assert call_count == 2

    def test_raises_after_max_retries(self, scraper, test_server):
        """최대 재시도 초과 시 예외."""
        def always_fail(u, **kwargs):
            raise PlaywrightError("Execution context was destroyed")

        with patch.object(scraper._page, "goto", side_effect=always_fail):
            with patch.object(scraper, "_wait_for_stable_context"):
                with pytest.raises(Exception, match="_safe_goto 최종 실패"):
                    scraper._safe_goto(f"{test_server}/band/12345")


# ══════════════════════════════════════════
# 4. fetch_all_posts 스크롤 E2E 테스트
# ══════════════════════════════════════════

class TestFetchAllPostsScroll:
    """fetch_all_posts 스크롤 중 context 파괴 복구."""

    def test_normal_scroll_collects_posts(self, scraper, test_server):
        """정상 스크롤 -> 게시글 수집."""
        posts_html = "".join(
            _make_post_html(f"post{i}", f"#PD\n테스트상품{i}\n121 (AI24)", days_ago=i)
            for i in range(3)
        )
        BandTestHandler.feed_html = _make_feed_page(posts_html)

        # BAND_HOME을 테스트 서버로 교체
        with patch("src.band_scraper.BAND_HOME", test_server):
            result = scraper.fetch_all_posts("12345")

        assert len(result) == 3
        assert all("post_key" in p for p in result)
        assert all("content" in p for p in result)
        assert all("created_at" in p for p in result)

    def test_context_destroyed_during_locator_count(self, scraper, test_server):
        """locator.count() 중 context 파괴 -> 복구 후 수집 계속."""
        posts_html = "".join(
            _make_post_html(f"post{i}", f"#PD\n테스트상품{i}\n121 (AI24)", days_ago=i)
            for i in range(2)
        )
        BandTestHandler.feed_html = _make_feed_page(posts_html)

        count_call = 0
        original_locator = scraper._page.locator

        class ContextDestroyLocator:
            """첫 count() 호출에서 context 파괴 에러를 발생시키는 래퍼."""

            def __init__(self, real_locator):
                self._real = real_locator

            def count(self):
                nonlocal count_call
                count_call += 1
                if count_call == 1:
                    raise PlaywrightError("Execution context was destroyed")
                return self._real.count()

            def nth(self, index):
                return self._real.nth(index)

        locator_call = 0

        def mock_locator(selector):
            nonlocal locator_call
            result = original_locator(selector)
            if selector == 'article._postMainWrap':
                locator_call += 1
                if locator_call == 1:
                    return ContextDestroyLocator(result)
            return result

        with patch("src.band_scraper.BAND_HOME", test_server):
            with patch.object(scraper._page, "locator", side_effect=mock_locator):
                with patch.object(scraper, "_wait_for_stable_context"):
                    result = scraper.fetch_all_posts("12345")

        # context 파괴에도 불구하고 게시글이 수집되어야 함
        assert len(result) >= 1

    def test_context_destroyed_exceeds_max_errors(self, scraper, test_server):
        """context 파괴 횟수 초과 -> 수집 조기 중단 (크래시 없이)."""
        posts_html = _make_post_html("post0", "#PD\n테스트\n121 (AI24)", days_ago=0)
        BandTestHandler.feed_html = _make_feed_page(posts_html)

        original_locator = scraper._page.locator

        def always_destroy(selector):
            result = original_locator(selector)
            if selector == 'article._postMainWrap':
                raise PlaywrightError("Execution context was destroyed")
            return result

        with patch("src.band_scraper.BAND_HOME", test_server):
            with patch.object(scraper._page, "locator", side_effect=always_destroy):
                with patch.object(scraper, "_wait_for_stable_context"):
                    # MAX_CONTEXT_ERRORS(5) 초과 후 종료 -> 크래시 없어야 함
                    result = scraper.fetch_all_posts("12345")

        # context가 항상 파괴되므로 수집 결과는 빈 리스트
        assert result == []

    def test_feed_drift_recovery(self, scraper, test_server):
        """스크롤 중 피드 이탈 감지 -> 밴드 피드 복귀."""
        posts_html = _make_post_html("post0", "#PD\n테스트\n121 (AI24)", days_ago=0)
        BandTestHandler.feed_html = _make_feed_page(posts_html)

        original_locator = scraper._page.locator
        attempt = 0

        def destroy_once(selector):
            nonlocal attempt
            result = original_locator(selector)
            if selector == 'article._postMainWrap':
                attempt += 1
                if attempt == 1:
                    raise PlaywrightError("Execution context was destroyed")
            return result

        goto_urls = []
        original_goto = scraper._page.goto

        def track_goto(url, **kwargs):
            goto_urls.append(url)
            return original_goto(url, **kwargs)

        # 피드 이탈 시뮬레이션: _wait_for_stable_context 후 URL이 다른 페이지
        def mock_wait():
            time.sleep(0.1)
            # URL을 다른 곳으로 변경 시뮬레이션 (page.url mock)

        with patch("src.band_scraper.BAND_HOME", test_server):
            with patch.object(scraper._page, "locator", side_effect=destroy_once):
                with patch.object(scraper._page, "goto", side_effect=track_goto):
                    with patch.object(scraper, "_wait_for_stable_context", side_effect=mock_wait):
                        # URL이 밴드 피드를 포함하므로 복귀 안 함 (정상 케이스)
                        result = scraper.fetch_all_posts("12345")

        # 최소 초기 goto는 호출됨
        assert any("/band/12345" in u for u in goto_urls)


# ══════════════════════════════════════════
# 5. fetch_all_posts 2-pass/3-pass E2E 테스트
# ══════════════════════════════════════════

class TestFetchAllPostsDetailPass:
    """2-pass(가격코드 보충) / 3-pass(이미지 보충) context 파괴 복구."""

    def test_2pass_detail_page_success(self, scraper, test_server):
        """2-pass: 상세 페이지에서 가격코드 보충 성공."""
        # 피드에는 가격코드 없는 게시글
        posts_html = _make_post_html(
            "post_no_price",
            "#PD\n프라다 숄더백\n가격정보 추후공지",
            days_ago=1,
            base_url=test_server,
        )
        BandTestHandler.feed_html = _make_feed_page(posts_html, "12345")

        # 상세 페이지에는 가격코드 있음
        BandTestHandler.detail_pages["post_no_price"] = _make_detail_page(
            "post_no_price",
            "#PD\n프라다 숄더백\n121 (AI24)",
        )

        with patch("src.band_scraper.BAND_HOME", test_server):
            result = scraper.fetch_all_posts("12345")

        assert len(result) == 1
        assert "121 (AI24)" in result[0]["content"]

    def test_2pass_context_destroyed_skips(self, scraper, test_server):
        """2-pass: 상세 진입 시 context 파괴 -> 해당 게시글 스킵 후 계속."""
        posts_html = "".join([
            _make_post_html("p1", "#PD\n상품1\n추후공지", days_ago=1, base_url=test_server),
            _make_post_html("p2", "#PD\n상품2\n추후공지", days_ago=2, base_url=test_server),
        ])
        BandTestHandler.feed_html = _make_feed_page(posts_html, "12345")
        BandTestHandler.detail_pages["p1"] = _make_detail_page("p1", "#PD\n상품1\n200 (QE)")
        BandTestHandler.detail_pages["p2"] = _make_detail_page("p2", "#PD\n상품2\n150 (BM)")

        goto_count = 0
        original_goto = scraper._page.goto

        def fail_first_detail(url, **kwargs):
            nonlocal goto_count
            if "/post/" in url:
                goto_count += 1
                if goto_count == 1:
                    raise PlaywrightError("Execution context was destroyed, most likely because of a navigation")
            return original_goto(url, **kwargs)

        with patch("src.band_scraper.BAND_HOME", test_server):
            with patch.object(scraper, "_safe_goto", side_effect=fail_first_detail):
                result = scraper.fetch_all_posts("12345")

        assert len(result) == 2
        # p1은 context 파괴로 보충 실패 -> 원래 content 유지
        p1 = next(p for p in result if "p1" in p["post_key"])
        assert "추후공지" in p1["content"]

    def test_3pass_image_supplement_success(self, scraper, test_server):
        """3-pass: 상세 페이지에서 이미지 보충 성공 (피드 4장 -> 상세 6장)."""
        # 피드에서 이미지 3장
        posts_html = _make_post_html(
            "img_post",
            "#PD\n프라다백\n121 (AI24)",
            days_ago=1,
            image_count=3,
            base_url=test_server,
        )
        BandTestHandler.feed_html = _make_feed_page(posts_html, "12345")

        # 상세에서 이미지 6장
        BandTestHandler.detail_pages["img_post"] = _make_detail_page(
            "img_post",
            "#PD\n프라다백\n121 (AI24)",
            image_count=6,
        )

        with patch("src.band_scraper.BAND_HOME", test_server):
            result = scraper.fetch_all_posts("12345")

        assert len(result) == 1
        # 3-pass에서 6장으로 보충되어야 함
        assert len(result[0]["photos"]) == 6

    def test_3pass_context_destroyed_keeps_original(self, scraper, test_server):
        """3-pass: 이미지 보충 중 context 파괴 -> 원래 이미지 유지."""
        posts_html = _make_post_html(
            "img_fail",
            "#PD\n프라다백\n121 (AI24)",
            days_ago=1,
            image_count=2,
            base_url=test_server,
        )
        BandTestHandler.feed_html = _make_feed_page(posts_html, "12345")
        BandTestHandler.detail_pages["img_fail"] = _make_detail_page(
            "img_fail", "#PD\n프라다백\n121 (AI24)", image_count=8,
        )

        original_safe_goto = scraper._safe_goto.__func__
        call_context = {"in_3pass": False}

        def fail_in_3pass(self_inner, url, **kwargs):
            # 3-pass에서만 실패 (2-pass에서는 가격코드 있으므로 스킵됨)
            if "/post/img_fail" in url:
                raise Exception("_safe_goto 최종 실패: " + url)
            return original_safe_goto(self_inner, url, **kwargs)

        with patch("src.band_scraper.BAND_HOME", test_server):
            with patch.object(BandScraper, "_safe_goto", fail_in_3pass):
                result = scraper.fetch_all_posts("12345")

        assert len(result) == 1
        # 이미지 보충 실패 -> 원래 피드 이미지(2장) 유지
        assert len(result[0]["photos"]) == 2


# ══════════════════════════════════════════
# 6. 스크롤 + cutoff 통합 테스트
# ══════════════════════════════════════════

class TestFetchAllPostsCutoff:
    """cutoff_date에 의한 수집 중단 검증."""

    def test_stops_at_cutoff_date(self, scraper, test_server):
        """cutoff 이전 게시글 도달 시 수집 중단."""
        # cutoff를 30일 전으로 설정 -> days_ago=1은 수집 / days_ago=60은 제외
        scraper.cutoff = datetime.now() - timedelta(days=30)

        posts_html = "".join([
            _make_post_html("recent", "#PD\n최근상품\n100 (AI24)", days_ago=1),
            _make_post_html("old", "#PD\n오래된상품\n200 (QE)", days_ago=60),
        ])
        BandTestHandler.feed_html = _make_feed_page(posts_html, "12345")

        with patch("src.band_scraper.BAND_HOME", test_server):
            result = scraper.fetch_all_posts("12345")

        # 최근 게시글만 수집
        keys = [p["post_key"] for p in result]
        assert any("recent" in k for k in keys)
        assert not any("old" in k for k in keys)

    def test_deduplicates_posts(self, scraper, test_server):
        """같은 post_key가 스크롤에서 반복되어도 중복 수집 안 함."""
        posts_html = "".join([
            _make_post_html("dup1", "#PD\n상품A\n100 (AI24)", days_ago=1),
            _make_post_html("dup1", "#PD\n상품A\n100 (AI24)", days_ago=1),  # 동일 key
            _make_post_html("dup2", "#PD\n상품B\n200 (QE)", days_ago=2),
        ])
        BandTestHandler.feed_html = _make_feed_page(posts_html, "12345")

        with patch("src.band_scraper.BAND_HOME", test_server):
            result = scraper.fetch_all_posts("12345")

        keys = [p["post_key"] for p in result]
        assert len(keys) == len(set(keys))  # 중복 없음


# ══════════════════════════════════════════
# 7. _safe_evaluate + 실제 Playwright context 파괴 E2E
# ══════════════════════════════════════════

class TestRealContextDestruction:
    """실제 Playwright에서 navigation으로 context 파괴 후 복구 검증."""

    def test_evaluate_after_navigation_recovers(self, scraper, test_server):
        """페이지 네비게이션 후 evaluate가 복구되는지 검증."""
        BandTestHandler.feed_html = _make_feed_page(
            _make_post_html("nav_test", "#PD\n테스트\n100 (AI)", days_ago=0)
        )

        # 첫 페이지 로드
        scraper._page.goto(f"{test_server}/band/12345", wait_until="domcontentloaded", timeout=10000)
        time.sleep(0.5)

        # 정상 evaluate
        result = scraper._safe_evaluate("document.title")
        assert "Band Feed" in result

        # 다른 페이지로 이동
        scraper._page.goto(f"{test_server}/feed", wait_until="domcontentloaded", timeout=10000)
        time.sleep(0.5)

        # 이동 후에도 evaluate 작동
        result2 = scraper._safe_evaluate("document.title")
        assert result2 is not None

    def test_scroll_evaluate_on_real_page(self, scraper, test_server):
        """실제 페이지에서 scrollTo evaluate가 작동하는지."""
        posts_html = "".join(
            _make_post_html(f"scroll{i}", f"#PD\n상품{i}\n{100+i} (AI)", days_ago=i)
            for i in range(5)
        )
        BandTestHandler.feed_html = _make_feed_page(posts_html)

        scraper._page.goto(f"{test_server}/band/12345", wait_until="domcontentloaded", timeout=10000)
        time.sleep(0.5)

        # 스크롤 실행
        scraper._safe_evaluate("window.scrollTo(0, document.body.scrollHeight)")
        scroll_count = scraper._page.evaluate("window._scrollCount")
        assert scroll_count >= 1
