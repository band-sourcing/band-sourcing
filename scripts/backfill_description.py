#!/usr/bin/env python3
"""
기존 WC 상품 description에 밴드 본문 텍스트 추가.

1. 밴드 재크롤링 (processed_posts 무시)
2. post_key로 DB의 wc_product_id 매칭
3. content 파싱 -> raw_content 추출
4. description 재구성 (공지이미지 + 본문 + 이미지) -> WC 업데이트

사용법:
  cd /opt/band-sourcing
  python3 scripts/backfill_description.py
"""

import sqlite3
import time
import logging
import sys
import os
import yaml
from dotenv import load_dotenv
from woocommerce import API
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.band_scraper import BandScraper
from src.content_parser import parse_post, ParseError, preprocess_content, _clean_raw_content

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("backfill")

load_dotenv()


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
        text_html = raw_content.replace('\n', '<br>')
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

    return '\n'.join(parts)


def main():
    settings = load_settings()
    band_keys = settings["band"].get("band_keys", {})
    notice_url_raw = settings.get("images", {}).get("notice_url", "")
    notice_url = validate_notice_url(notice_url_raw)

    if notice_url:
        logger.info(f"공지 이미지 확인됨: {notice_url}")
    else:
        logger.warning("공지 이미지 접근 불가")

    # DB에서 기존 상품의 post_key -> wc_product_id 매핑 로드
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    )
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute(
        "SELECT wc_product_id, post_key FROM products "
        "WHERE wc_product_id IS NOT NULL"
    )
    postkey_to_wcid = {}
    for row in cur.fetchall():
        pk = row["post_key"]
        if pk not in postkey_to_wcid:
            postkey_to_wcid[pk] = []
        postkey_to_wcid[pk].append(row["wc_product_id"])
    db.close()

    logger.info(f"DB 상품: {sum(len(v) for v in postkey_to_wcid.values())}개 / post_key: {len(postkey_to_wcid)}개")

    # WC API
    api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=120
    )

    # 밴드 크롤러 초기화
    scraper = BandScraper(
        naver_id=os.getenv("NAVER_ID", ""),
        naver_pw=os.getenv("NAVER_PW", ""),
        cutoff_date=settings["band"]["cutoff_date"],
        session_path="band_session.json",
    )

    brand_map = settings.get("brand_map", {})
    update_success = 0
    update_fail = 0
    no_match = 0
    parse_errors = 0

    try:
        scraper.ensure_logged_in()

        for band_name, band_key in band_keys.items():
            logger.info(f"\n=== {band_name} (key={band_key}) 크롤링 시작 ===")

            posts = scraper.fetch_all_posts(band_key)
            logger.info(f"  수집: {len(posts)}개")

            for post in posts:
                post_key = post["post_key"]

                # DB 매칭 확인
                if post_key not in postkey_to_wcid:
                    no_match += 1
                    continue

                wc_ids = postkey_to_wcid[post_key]
                content = post.get("content", "")
                if not content:
                    continue

                # raw_content 추출 (민감정보 제거)
                cleaned = preprocess_content(content)
                raw_content = _clean_raw_content(cleaned)

                if not raw_content:
                    continue

                # 게시글 이미지 URL
                photo_urls = [
                    p["url"] for p in post.get("photos", [])
                    if not p.get("is_video_thumbnail", False)
                ]

                # 매칭된 WC 상품 전부 업데이트
                for wc_id in wc_ids:
                    try:
                        new_desc = build_description(notice_url, raw_content, photo_urls)

                        # 이미지는 첫 장만 (목록 썸네일)
                        new_images = []
                        if photo_urls:
                            new_images = [{"src": photo_urls[0], "position": 0}]

                        resp = api.put(f"products/{wc_id}", {
                            "description": new_desc,
                            "images": new_images,
                        })

                        if resp.status_code == 200:
                            update_success += 1
                        else:
                            update_fail += 1
                            logger.error(f"  업데이트 실패 ID={wc_id}: {resp.status_code}")

                    except Exception as e:
                        update_fail += 1
                        logger.error(f"  업데이트 에러 ID={wc_id}: {e}")

                    time.sleep(1)

                if (update_success + update_fail) % 50 == 0 and (update_success + update_fail) > 0:
                    logger.info(f"  진행: 성공={update_success} 실패={update_fail} 매칭안됨={no_match}")

    except Exception as e:
        logger.critical(f"치명적 에러: {e}", exc_info=True)
    finally:
        scraper.close()

    logger.info(f"\n=== 완료 ===")
    logger.info(f"  description 업데이트 성공: {update_success}")
    logger.info(f"  description 업데이트 실패: {update_fail}")
    logger.info(f"  post_key 매칭 안 됨: {no_match}")
    logger.info(f"  파싱 에러: {parse_errors}")


if __name__ == "__main__":
    main()
