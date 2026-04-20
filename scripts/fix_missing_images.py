#!/usr/bin/env python3
"""
이미지 없는 상품 대상 밴드 게시글 재방문 -> 이미지 추출 -> WC 썸네일+description 업데이트.

fix_common_images.py에서 공통 이미지 제거 후 실제 상품 이미지가 없는 35개 대상.
post_key로 밴드 게시글에 직접 접근하여 이미지를 추출한다.

사용법:
  cd /opt/band-sourcing
  python3 scripts/fix_missing_images.py --dry-run
  python3 scripts/fix_missing_images.py
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
import time

import yaml
from dotenv import load_dotenv
from woocommerce import API

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.band_scraper import BandScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fix_missing_images")

load_dotenv()

# 제거 대상 이미지 패턴 (description에 넣지 않을 것들)
SKIP_IMG_PATTERNS = [
    "a_h9hUd018svc19uvfzsdn00w9_4k8958",  # 밴드 프로필 (고양이)
    "_5ksoqj",  # 밴드 공통 png
    "_fwc5at",  # 밴드 하단 고정
    "profile", "avatar", "icon", "emoji",
    "sticker", "emoticon", "gif_origin",
    "logo", "1x1",
]

MD5_PATTERN = re.compile(r'^[0-9a-f]{16}$', re.IGNORECASE)


def load_settings():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def should_skip_img(url):
    low = url.lower()
    return any(p in low for p in SKIP_IMG_PATTERNS)


def extract_images_from_post(page, band_key, post_id, max_retries=3):
    """개별 게시글 URL 방문 -> 상품 이미지만 추출."""
    detail_url = f"https://band.us/band/{band_key}/post/{post_id}"

    for attempt in range(max_retries):
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            # 더보기 클릭 시도
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

            photo_urls = []
            seen_urls = set()

            # img._image 우선
            img_elements = page.locator("img._image")
            count = img_elements.count()
            for i in range(count):
                try:
                    img = img_elements.nth(i)
                    src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                    if not src or not src.startswith("http"):
                        continue
                    if should_skip_img(src) or src in seen_urls:
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
                        if should_skip_img(src) or src in seen_urls:
                            continue
                        if "phinf" not in src:
                            continue
                        seen_urls.add(src)
                        photo_urls.append(BandScraper._get_full_res_url(src))
                    except Exception:
                        continue

            return photo_urls

        except Exception as e:
            logger.warning(f"  접근 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(3)

    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings()

    wc_api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=120,
    )

    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    )

    # ── Step 1: 이미지 없는 상품 찾기 (WC에서 images가 비어있는 것) ──
    logger.info("이미지 없는 상품 조회 중...")

    no_image_products = []
    page = 1

    while True:
        resp = wc_api.get(
            "products",
            params={"per_page": 100, "page": page, "_fields": "id,name,images"},
        )
        if resp.status_code != 200:
            break
        products = resp.json()
        if not products:
            break
        for p in products:
            imgs = p.get("images", [])
            if not imgs:
                no_image_products.append(p)
        page += 1

    logger.info(f"이미지 없는 상품: {len(no_image_products)}개")

    if not no_image_products:
        logger.info("처리할 상품 없음 -> 종료")
        return

    # ── Step 2: DB에서 post_key 매핑 ──
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    wcid_to_postkey = {}
    for p in no_image_products:
        cur.execute(
            "SELECT post_key, band_key FROM products WHERE wc_product_id = ?",
            (p["id"],),
        )
        row = cur.fetchone()
        if row:
            wcid_to_postkey[p["id"]] = {
                "post_key": row["post_key"],
                "band_key": row["band_key"],
                "name": p["name"],
            }
    db.close()

    logger.info(f"DB 매칭: {len(wcid_to_postkey)}개 / 매칭안됨: {len(no_image_products) - len(wcid_to_postkey)}개")

    # post_key에서 post_id 추출 + md5 필터
    targets = []
    md5_skipped = 0
    for wc_id, info in wcid_to_postkey.items():
        post_key = info["post_key"]
        parts = post_key.split("_", 1)
        if len(parts) != 2:
            md5_skipped += 1
            continue
        band_key, post_id = parts
        if MD5_PATTERN.match(post_id):
            md5_skipped += 1
            continue
        targets.append({
            "wc_id": wc_id,
            "band_key": band_key,
            "post_id": post_id,
            "name": info["name"],
        })

    logger.info(f"밴드 접근 대상: {len(targets)}개 / md5 스킵: {md5_skipped}개")

    if not targets:
        logger.info("처리할 대상 없음 -> 종료")
        return

    # ── Step 3: 밴드 접근 + 이미지 추출 + WC 업데이트 ──
    scraper = BandScraper(
        naver_id=os.getenv("NAVER_ID", ""),
        naver_pw=os.getenv("NAVER_PW", ""),
        cutoff_date=settings["band"]["cutoff_date"],
        session_path=os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "band_session.json"
        ),
    )

    success = 0
    no_img_found = 0
    errors = 0

    try:
        scraper.ensure_logged_in()
        browser_page = scraper._page

        for idx, t in enumerate(targets):
            logger.info(
                f"  [{idx + 1}/{len(targets)}] wc_id={t['wc_id']} "
                f"post={t['band_key']}_{t['post_id']} -> {t['name'][:30]}"
            )

            photo_urls = extract_images_from_post(
                browser_page, t["band_key"], t["post_id"]
            )

            if not photo_urls:
                no_img_found += 1
                logger.warning(f"    이미지 없음 -> 스킵")
                time.sleep(1)
                continue

            # 공통 이미지 한번 더 필터
            clean_urls = [u for u in photo_urls if not should_skip_img(u)]
            if not clean_urls:
                no_img_found += 1
                logger.warning(f"    필터 후 이미지 없음 -> 스킵")
                time.sleep(1)
                continue

            logger.info(f"    이미지 {len(clean_urls)}장 추출")

            if args.dry_run:
                success += 1
                logger.info(f"    [DRY-RUN] 업데이트 스킵")
                time.sleep(1)
                continue

            # WC 업데이트: 썸네일 + description에 이미지 추가
            try:
                # 기존 description 가져오기
                resp_get = wc_api.get(
                    f"products/{t['wc_id']}",
                    params={"_fields": "description"},
                )
                current_desc = resp_get.json().get("description", "") if resp_get.status_code == 200 else ""

                # description 끝에 이미지 추가
                img_html = "\n".join(
                    f'<img src="{url}" style="width:100%; margin-bottom:10px;" alt="상품 이미지">'
                    for url in clean_urls
                )
                new_desc = current_desc + "\n" + img_html

                payload = {
                    "description": new_desc,
                    "images": [{"src": clean_urls[0], "position": 0}],
                }

                resp_put = wc_api.put(f"products/{t['wc_id']}", payload)
                if resp_put.status_code == 200:
                    success += 1
                    logger.info(f"    ✅ 업데이트 성공 (이미지 {len(clean_urls)}장)")
                else:
                    errors += 1
                    logger.error(f"    ❌ 업데이트 실패: {resp_put.status_code}")
            except Exception as e:
                errors += 1
                logger.error(f"    ❌ 에러: {e}")

            time.sleep(1.5)

    except Exception as e:
        logger.critical(f"치명적 에러: {e}", exc_info=True)
    finally:
        scraper.close()

    logger.info("")
    logger.info("=" * 50)
    prefix = "[DRY-RUN] " if args.dry_run else ""
    logger.info(f"{prefix}완료 리포트")
    logger.info(f"  이미지 추출+업데이트 성공: {success}개")
    logger.info(f"  밴드에서도 이미지 없음: {no_img_found}개")
    logger.info(f"  에러: {errors}개")
    logger.info(f"  md5 스킵: {md5_skipped}개")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
