#!/usr/bin/env python3
"""
[Step 4 FIX-F] 기존 잡화천국22 상품 이미지 보충.

이미 WC에 등록된 잡화천국22 상품 중 이미지 4장 이하인 상품의
description을 재구성하여 이미지를 보충한다.

이미지 보충 방식:
1. DB에서 잡화천국22 상품 조회
2. WC에서 해당 상품의 images 배열 길이 확인 (4장 이하만 대상)
3. 밴드 상세 페이지 방문 → 이미지 전체 추출
4. description HTML 재구성 (공지이미지 + 본문텍스트 + 이미지 전체)
5. WC 업데이트

사용법:
  cd /opt/band-sourcing
  source venv/bin/activate
  python3 scripts/supplement_images.py [--dry-run] [--limit N]

  --dry-run: 실제 업데이트 없이 대상만 출력
  --limit N: 최대 N개 상품만 처리
"""

import sqlite3
import time
import logging
import sys
import os
import re
import argparse
import yaml
from dotenv import load_dotenv
from woocommerce import API

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("supplement_images")

load_dotenv()

# 잡화천국22 밴드 키
_JABHWA_BAND_KEY = "97874828"
_BAND_URL_BASE = "https://band.us/band"

# 이미지 필터 패턴 (밴드 공통 이미지 제외)
SKIP_PATTERNS = [
    "common-cover",
    "res.band.us",
    "band-attfile",
    "sticker",
    "emoji",
    "profile",
]


def load_settings():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_product_image(url: str) -> bool:
    """밴드 공통 이미지가 아닌 상품 이미지인지 확인."""
    url_lower = url.lower()
    for pat in SKIP_PATTERNS:
        if pat in url_lower:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="잡화천국22 이미지 보충")
    parser.add_argument("--dry-run", action="store_true", help="실제 업데이트 없이 대상만 출력")
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 건수 (0=전체)")
    args = parser.parse_args()

    settings = load_settings()
    notice_url = settings.get("images", {}).get("notice_url", "")

    api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=120
    )

    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    )
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    # 잡화천국22 상품 조회
    cur.execute(
        "SELECT id, wc_product_id, brand_tag, product_name, post_key "
        "FROM products WHERE band_key = ? AND wc_product_id IS NOT NULL",
        (_JABHWA_BAND_KEY,)
    )
    rows = cur.fetchall()
    logger.info(f"잡화천국22 상품: {len(rows)}개")

    # WC에서 이미지 4장 이하인 상품 필터
    targets = []
    for idx, row in enumerate(rows):
        wc_id = row["wc_product_id"]
        try:
            resp = api.get(f"products/{wc_id}")
            if resp.status_code != 200:
                continue
            data = resp.json()
            images = data.get("images", [])

            if len(images) <= 4:
                targets.append({
                    "db_id": row["id"],
                    "wc_id": wc_id,
                    "brand": row["brand_tag"],
                    "name": row["product_name"],
                    "post_key": row["post_key"],
                    "current_images": len(images),
                    "description": data.get("description", ""),
                })
        except Exception as e:
            logger.error(f"  WC 조회 실패 #{wc_id}: {e}")

        if (idx + 1) % 20 == 0:
            time.sleep(1)
            logger.info(f"  스캔 진행: {idx+1}/{len(rows)}")

    logger.info(f"\n이미지 보충 대상: {len(targets)}건 (≤4장)")

    if args.limit > 0:
        targets = targets[:args.limit]
        logger.info(f"  --limit {args.limit} 적용 → {len(targets)}건만 처리")

    if args.dry_run:
        logger.info("(dry-run 모드 - 실제 업데이트 없음)")
        for t in targets:
            logger.info(
                f"  [보충대상] {t['brand']} {t['name']} | "
                f"현재 {t['current_images']}장 | WC#{t['wc_id']}"
            )
        db.close()
        return

    # 이미지 보충은 밴드 상세 페이지 방문이 필요 → Playwright 사용
    # 여기서는 BandScraper를 import해서 사용
    try:
        from src.band_scraper import BandScraper
    except ImportError:
        logger.error("BandScraper import 실패 - playwright 설치 확인 필요")
        logger.error("  pip install playwright && playwright install chromium")
        db.close()
        return

    scraper = BandScraper()
    try:
        scraper.login()
    except Exception as e:
        logger.error(f"밴드 로그인 실패: {e}")
        scraper.close()
        db.close()
        return

    updated = 0
    failed = 0

    for t in targets:
        # post_key에서 밴드 게시글 URL 구성
        # post_key 형태: "97874828_postkey"
        post_key_parts = t["post_key"].split("_", 1)
        if len(post_key_parts) < 2:
            logger.warning(f"  post_key 파싱 실패: {t['post_key']}")
            failed += 1
            continue

        band_key = post_key_parts[0]
        raw_post_key = post_key_parts[1]
        post_url = f"{_BAND_URL_BASE}/{band_key}/post/{raw_post_key}"

        try:
            scraper._page.goto(post_url, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)

            # 상세 페이지에서 이미지 추출
            detail_el = scraper._page.locator('article._postMainWrap').first
            if detail_el.count() == 0:
                detail_el = scraper._page.locator('.dPostBody, ._postBody, .postBody').first

            if detail_el.count() == 0:
                logger.warning(f"  게시글 컨테이너 없음: {t['name']}")
                failed += 1
                continue

            photos = scraper._extract_photos(detail_el)
            product_photos = [p for p in photos if is_product_image(p["url"])]

            if len(product_photos) <= t["current_images"]:
                logger.info(f"  이미지 변화 없음: {t['name']} ({len(product_photos)}장)")
                continue

            # description 재구성: 공지이미지 + 기존 텍스트 + 새 이미지 전체
            # 기존 description에서 텍스트 부분만 추출
            existing_desc = t["description"]
            # img 태그 제거하고 텍스트만
            text_only = re.sub(r'<img[^>]*>', '', existing_desc)
            text_only = text_only.strip()

            # 새 description 구성
            parts = []
            if notice_url:
                parts.append(f'<img src="{notice_url}" alt="공지" />')
            if text_only:
                parts.append(text_only)
            for photo in product_photos:
                parts.append(f'<img src="{photo["url"]}" alt="상품이미지" />')

            new_desc = "<br/>".join(parts)

            # WC 업데이트
            update_data = {"description": new_desc}
            # 대표사진(첫 번째 이미지)도 업데이트
            if product_photos:
                update_data["images"] = [{"src": product_photos[0]["url"], "position": 0}]

            resp = api.put(f"products/{t['wc_id']}", update_data)
            if resp.status_code == 200:
                updated += 1
                logger.info(
                    f"  보충 완료: {t['brand']} {t['name']} | "
                    f"{t['current_images']}→{len(product_photos)}장"
                )
            else:
                failed += 1
                logger.error(f"  WC 업데이트 실패: {resp.status_code}")

        except Exception as e:
            failed += 1
            logger.error(f"  처리 실패: {t['name']} ({e})")

        time.sleep(1)

    scraper.close()
    db.close()

    logger.info(f"\n=== 이미지 보충 완료 ===")
    logger.info(f"  보충: {updated}건 / 실패: {failed}건")


if __name__ == "__main__":
    main()
