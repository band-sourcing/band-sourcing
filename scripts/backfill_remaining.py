#!/usr/bin/env python3
"""
미백필 상품 description 보충 (개별 게시글 URL 직접 방문 방식).

기존 backfill_description.py가 스크롤 한계로 도달 못한 ~211개 상품 대상.
스크롤 대신 post_key에서 post_id를 추출하여 개별 URL로 직접 접근한다.

사용법:
  cd /opt/band-sourcing
  python3 scripts/backfill_remaining.py

옵션:
  --dry-run    실제 WC 업데이트 없이 추출만 확인
  --limit N    처리할 최대 상품 수 (테스트용)
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time

import httpx
import yaml
from dotenv import load_dotenv
from woocommerce import API

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.content_parser import preprocess_content, _clean_raw_content
from src.band_scraper import BandScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backfill_remaining")

load_dotenv()

# post_key가 md5 해시인지 판별 (16자 hex → URL 구성 불가)
MD5_PATTERN = re.compile(r'^[0-9a-f]{16}$', re.IGNORECASE)


def load_settings():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_notice_url(url):
    if not url:
        return None
    try:
        resp = httpx.head(url, timeout=10, follow_redirects=True)
        return url if resp.status_code == 200 else None
    except Exception:
        return None


def build_description(notice_url, raw_content, photo_urls):
    """공지이미지 + 본문텍스트 + 이미지 세로나열."""
    parts = []

    if notice_url:
        parts.append(
            f'<img src="{notice_url}" '
            f'style="width:100%; margin-bottom:20px;" '
            f'alt="공지사항">'
        )

    if raw_content:
        text_html = raw_content.replace("\n", "<br>")
        parts.append(
            f'<div style="margin-bottom:20px; line-height:1.8; '
            f'font-size:14px;">{text_html}</div>'
        )

    for url in photo_urls:
        parts.append(
            f'<img src="{url}" '
            f'style="width:100%; margin-bottom:10px;" '
            f'alt="상품 이미지">'
        )

    return "\n".join(parts)


# ── TASK-1: 미백필 상품 목록 추출 ──


def fetch_unfilled_products(db_path, wc_api, batch_size=100):
    """
    DB에서 wc_product_id가 있는 모든 상품을 가져온 뒤
    WC API로 description을 조회하여 본문 텍스트 div가 없는 것을 필터링.
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute(
        "SELECT id, wc_product_id, post_key, band_key, product_name "
        "FROM products WHERE wc_product_id IS NOT NULL"
    )
    all_products = [dict(row) for row in cur.fetchall()]
    db.close()

    logger.info(f"DB 전체 상품: {len(all_products)}개")

    unfilled = []
    wc_ids = [p["wc_product_id"] for p in all_products]

    # WC API batch 조회 (100개씩)
    wc_desc_map = {}
    for i in range(0, len(wc_ids), batch_size):
        batch = wc_ids[i : i + batch_size]
        include_str = ",".join(str(x) for x in batch)
        try:
            resp = wc_api.get(
                "products",
                params={
                    "include": include_str,
                    "per_page": batch_size,
                    "_fields": "id,description",
                },
            )
            if resp.status_code == 200:
                for item in resp.json():
                    wc_desc_map[item["id"]] = item.get("description", "")
            else:
                logger.warning(f"WC batch 조회 실패 ({resp.status_code}) batch={i}")
        except Exception as e:
            logger.warning(f"WC batch 조회 에러: {e}")
        time.sleep(0.5)

    logger.info(f"WC description 조회 완료: {len(wc_desc_map)}개")

    # 본문 텍스트 div가 없는 상품 = 미백필
    # 기존 build_description은 raw_content가 있으면
    # <div style="margin-bottom:20px; line-height:1.8 ..."> 를 넣음
    for product in all_products:
        wc_id = product["wc_product_id"]
        desc = wc_desc_map.get(wc_id, "")

        # description에 본문 div가 있으면 이미 백필된 것
        if '<div style="margin-bottom:20px; line-height:1.8' in desc:
            continue

        unfilled.append(product)

    logger.info(f"미백필 상품: {len(unfilled)}개")
    return unfilled


# ── TASK-2: 개별 게시글 직접 접근 ──


def extract_post_detail(page, band_key, post_id, max_retries=3):
    """
    개별 게시글 URL로 직접 접근하여 본문 텍스트 + 이미지 추출.
    Returns: (raw_content, photo_urls) or (None, [])
    """
    detail_url = f"https://band.us/band/{band_key}/post/{post_id}"

    for attempt in range(max_retries):
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            # "더보기" 버튼 클릭 시도
            try:
                more_btn = page.locator(
                    'button:has-text("더보기"), '
                    'a:has-text("더보기"), '
                    '[class*="more"]'
                ).first
                if more_btn.count() > 0 and more_btn.is_visible():
                    more_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # 본문 텍스트 추출
            content = ""
            text_selectors = [
                "p.txtBody",
                ".postText",
                "._postText",
                '[class*="postText"]',
                '[class*="txtBody"]',
                ".dPostBody",
                "._postBody",
            ]

            for sel in text_selectors:
                text_el = page.locator(sel).first
                if text_el.count() > 0:
                    text = text_el.inner_text().strip()
                    if text and len(text) > 3:
                        content = text
                        break

            # 이미지 추출
            photo_urls = []
            seen_urls = set()

            SKIP_PATTERNS = [
                "profile", "avatar", "icon", "emoji",
                "sticker", "emoticon", "gif_origin",
                "logo", "1x1",
            ]

            def should_skip(url):
                low = url.lower()
                return any(skip in low for skip in SKIP_PATTERNS)

            # img._image 우선
            img_elements = page.locator("img._image")
            count = img_elements.count()
            for i in range(count):
                try:
                    img = img_elements.nth(i)
                    src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                    if not src or not src.startswith("http"):
                        continue
                    if should_skip(src) or src in seen_urls:
                        continue
                    seen_urls.add(src)
                    photo_urls.append(BandScraper._get_full_res_url(src))
                except Exception:
                    continue

            # fallback: phinf 이미지
            if not photo_urls:
                all_imgs = page.locator("img")
                count = all_imgs.count()
                for i in range(count):
                    try:
                        img = all_imgs.nth(i)
                        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                        if not src or not src.startswith("http"):
                            continue
                        if should_skip(src) or src in seen_urls:
                            continue
                        if "phinf" not in src:
                            continue
                        seen_urls.add(src)
                        photo_urls.append(BandScraper._get_full_res_url(src))
                    except Exception:
                        continue

            return content, photo_urls

        except Exception as e:
            logger.warning(
                f"  게시글 접근 실패 (시도 {attempt + 1}/{max_retries}): "
                f"{detail_url} -> {e}"
            )
            if attempt < max_retries - 1:
                time.sleep(3)

    return None, []


# ── TASK-3: description 재구성 + WC 업데이트 ──


def update_wc_description(wc_api, wc_id, notice_url, raw_content, photo_urls):
    """WC 상품 description 업데이트."""
    new_desc = build_description(notice_url, raw_content, photo_urls)

    payload = {"description": new_desc}
    if photo_urls:
        payload["images"] = [{"src": photo_urls[0], "position": 0}]

    resp = wc_api.put(f"products/{wc_id}", payload)
    return resp.status_code == 200, resp.status_code


def main():
    parser = argparse.ArgumentParser(description="미백필 상품 description 보충")
    parser.add_argument("--dry-run", action="store_true", help="실제 WC 업데이트 없이 추출만 확인")
    parser.add_argument("--limit", type=int, default=0, help="처리할 최대 상품 수 (0=전체)")
    args = parser.parse_args()

    settings = load_settings()
    notice_url_raw = settings.get("images", {}).get("notice_url", "")
    notice_url = validate_notice_url(notice_url_raw)

    if notice_url:
        logger.info(f"공지 이미지 확인됨: {notice_url}")
    else:
        logger.warning("공지 이미지 접근 불가 -> 공지 이미지 없이 진행")

    # WC API 초기화
    wc_api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=120,
    )

    # DB 경로
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    )

    # ── TASK-1: 미백필 목록 추출 ──
    logger.info("=" * 50)
    logger.info("[TASK-1] 미백필 상품 목록 추출 시작")
    unfilled = fetch_unfilled_products(db_path, wc_api)

    if not unfilled:
        logger.info("미백필 상품 없음 -> 종료")
        return

    if args.limit > 0:
        unfilled = unfilled[: args.limit]
        logger.info(f"--limit {args.limit} 적용 -> {len(unfilled)}개만 처리")

    # post_key 분류: URL 접근 가능 vs md5 해시 (스킵)
    url_accessible = []
    md5_skipped = []

    for product in unfilled:
        post_key = product["post_key"]
        parts = post_key.split("_", 1)

        if len(parts) != 2:
            md5_skipped.append(product)
            continue

        band_key, post_id = parts

        # post_id가 md5 해시 패턴이면 URL 구성 불가
        if MD5_PATTERN.match(post_id):
            md5_skipped.append(product)
            continue

        product["_band_key"] = band_key
        product["_post_id"] = post_id
        url_accessible.append(product)

    logger.info(
        f"[TASK-1] 완료: URL접근가능={len(url_accessible)} / "
        f"md5스킵={len(md5_skipped)} / 전체={len(unfilled)}"
    )

    if not url_accessible:
        logger.info("URL 접근 가능한 상품 없음 -> 종료")
        _print_report(0, 0, len(md5_skipped), [])
        return

    # ── TASK-2 & 3: 개별 게시글 접근 + WC 업데이트 ──
    logger.info("=" * 50)
    logger.info("[TASK-2/3] 개별 게시글 접근 + description 업데이트 시작")

    scraper = BandScraper(
        naver_id=os.getenv("NAVER_ID", ""),
        naver_pw=os.getenv("NAVER_PW", ""),
        cutoff_date=settings["band"]["cutoff_date"],
        session_path=os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "band_session.json"
        ),
    )

    update_success = 0
    update_fail = 0
    extract_fail = 0
    errors = []

    try:
        scraper.ensure_logged_in()
        page = scraper._page

        for idx, product in enumerate(url_accessible):
            wc_id = product["wc_product_id"]
            band_key = product["_band_key"]
            post_id = product["_post_id"]
            pname = product["product_name"]

            logger.info(
                f"  [{idx + 1}/{len(url_accessible)}] "
                f"wc_id={wc_id} post={band_key}_{post_id} -> {pname}"
            )

            # 게시글 직접 접근
            content, photo_urls = extract_post_detail(page, band_key, post_id)

            if not content:
                extract_fail += 1
                logger.warning(f"    본문 추출 실패 -> 스킵")
                time.sleep(1)
                continue

            # 민감정보 제거
            cleaned = preprocess_content(content)
            raw_content = _clean_raw_content(cleaned)

            if not raw_content:
                extract_fail += 1
                logger.warning(f"    민감정보 제거 후 본문 없음 -> 스킵")
                time.sleep(1)
                continue

            logger.info(
                f"    추출 성공: 본문 {len(raw_content)}자 / 이미지 {len(photo_urls)}장"
            )

            if args.dry_run:
                update_success += 1
                logger.info(f"    [DRY-RUN] 업데이트 스킵")
            else:
                ok, status_code = update_wc_description(
                    wc_api, wc_id, notice_url, raw_content, photo_urls
                )
                if ok:
                    update_success += 1
                    logger.info(f"    WC 업데이트 성공")
                else:
                    update_fail += 1
                    err_msg = f"wc_id={wc_id} status={status_code}"
                    errors.append(err_msg)
                    logger.error(f"    WC 업데이트 실패: {status_code}")

            # rate limit 방지
            time.sleep(1.5)

            # 진행률 로그
            if (idx + 1) % 20 == 0:
                logger.info(
                    f"  --- 진행: {idx + 1}/{len(url_accessible)} "
                    f"(성공={update_success} 실패={update_fail} 추출실패={extract_fail})"
                )

    except Exception as e:
        logger.critical(f"치명적 에러: {e}", exc_info=True)
        errors.append(f"CRITICAL: {e}")
    finally:
        scraper.close()

    _print_report(update_success, update_fail, len(md5_skipped), errors, extract_fail)


def _print_report(success, fail, md5_skip, errors, extract_fail=0):
    logger.info("")
    logger.info("=" * 50)
    logger.info("✅ [Step 2] Completion Report")
    logger.info("▶ Task Summary: 미백필 상품 description 보충")
    logger.info("▶ Deliverables: scripts/backfill_remaining.py")
    logger.info("▶ Items Delivered:")
    logger.info("  ✅ TASK-1: 미백필 목록 추출")
    logger.info("  ✅ TASK-2: 개별 게시글 직접 접근 스크립트")
    logger.info("  ✅ TASK-3: description 재구성 + WC 업데이트")
    logger.info(
        f"▶ 실행 결과: 성공 {success}개 / 실패 {fail}개 / "
        f"추출실패 {extract_fail}개 / 스킵(md5) {md5_skip}개"
    )
    if errors:
        logger.info(f"▶ Errors: {errors[:10]}")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
