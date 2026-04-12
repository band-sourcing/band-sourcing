#!/usr/bin/env python3
"""
기존 WC 상품 일괄 업데이트 스크립트.
- 상품명: 영문 브랜드 → 한글 브랜드 변환
- 카테고리: margin category → WC category ID 매핑
- description: 공지이미지 + 본문(없으면 빈칸) + 상품이미지 세로나열

사용법:
  cd /opt/band-sourcing
  python3 scripts/batch_update_existing.py

주의: WC API rate limit 고려하여 배치 처리 + sleep 포함
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("batch_update")

load_dotenv()


def load_settings():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_wc_category(category, product_name, wc_cat_config):
    """마진 카테고리 + 상품명 → WC 카테고리 ID."""
    cat_conf = wc_cat_config.get(category, {})

    if category == "bag_watch":
        watch_keywords = cat_conf.get("watch_keywords", ["시계", "워치"])
        for kw in watch_keywords:
            if kw in product_name:
                return cat_conf.get("watch_id", 86)
        return cat_conf.get("bag_id", 85)
    elif category == "outer":
        return cat_conf.get("id", 78)
    else:
        etc_conf = wc_cat_config.get("etc", {})
        return etc_conf.get("id", 89)


def convert_brand_name(brand_tag, product_name, brand_map):
    """기존 [ENGLISH] 상품명 → [한글] 상품명으로 변환."""
    korean_name = brand_map.get(brand_tag)
    if not korean_name:
        return None  # 매핑 없으면 변경 안 함

    # 기존 상품명에서 [브랜드] 부분 제거하고 한글로 교체
    # 패턴: [ANYTHING] 나머지상품명
    import re
    m = re.match(r'^\[([^\]]+)\]\s*(.*)$', product_name)
    if m:
        raw_name = m.group(2)
        return f"[{korean_name}] {raw_name}"
    else:
        # 대괄호 없는 경우 그냥 앞에 붙이기
        return f"[{korean_name}] {product_name}"


def validate_notice_url(url):
    """공지 이미지 URL 유효성 확인."""
    if not url:
        return None
    try:
        resp = httpx.head(url, timeout=10, follow_redirects=True)
        if resp.status_code == 200:
            return url
        return None
    except Exception:
        return None


def build_description(notice_url, photo_urls):
    """
    description HTML 생성.
    기존 상품은 raw_content(본문)가 DB에 없으므로 공지이미지 + 이미지 세로나열만.
    """
    parts = []

    if notice_url:
        parts.append(
            f'<img src="{notice_url}" '
            f'style="width:100%; margin-bottom:20px;" '
            f'alt="공지사항">'
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
    brand_map = settings.get("brand_map", {})
    wc_cat_config = settings.get("wc_categories", {})
    notice_url_raw = settings.get("images", {}).get("notice_url", "")

    api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=120
    )

    # 공지 이미지 유효성 확인
    notice_url = validate_notice_url(notice_url_raw)
    if notice_url:
        logger.info(f"공지 이미지 확인됨: {notice_url}")
    else:
        logger.warning(f"공지 이미지 접근 불가: {notice_url_raw}")

    # DB에서 기존 상품 전체 조회
    db = sqlite3.connect(os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    ))
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute(
        "SELECT wc_product_id, brand_tag, product_name, category "
        "FROM products WHERE wc_product_id IS NOT NULL"
    )
    rows = cur.fetchall()
    db.close()

    total = len(rows)
    logger.info(f"업데이트 대상: {total}개")

    success = 0
    fail = 0
    skip = 0
    batch_size = 10

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        update_list = []

        for row in batch:
            wc_id = row["wc_product_id"]
            brand_tag = row["brand_tag"]
            product_name = row["product_name"]
            category = row["category"]

            update_data = {"id": wc_id}
            has_change = False

            # 1) 상품명 한글 변환
            new_name = convert_brand_name(brand_tag, product_name, brand_map)
            if new_name:
                update_data["name"] = new_name
                update_data["short_description"] = new_name
                has_change = True

            # 2) 카테고리 매핑
            wc_cat_id = resolve_wc_category(category, product_name, wc_cat_config)
            update_data["categories"] = [{"id": wc_cat_id}]
            has_change = True

            # 3) description 재구성 (WC에서 기존 이미지 URL 가져와야 함)
            # -> 배치 업데이트에서는 개별 상품 이미지 조회가 필요하므로
            #    description은 별도 처리

            if has_change:
                update_list.append(update_data)

        if not update_list:
            skip += len(batch)
            continue

        # 배치 업데이트 (상품명 + 카테고리)
        try:
            resp = api.post("products/batch", {"update": update_list})
            if resp.status_code == 200:
                success += len(update_list)
            else:
                fail += len(update_list)
                logger.error(f"배치 실패: {resp.status_code}")
        except Exception as e:
            fail += len(update_list)
            logger.error(f"배치 에러: {e}")

        logger.info(f"  [{i + len(batch)}/{total}] 성공={success} 실패={fail} 건너뜀={skip}")
        time.sleep(2)

    logger.info(f"\n=== 1단계 완료 (상품명 + 카테고리): 성공={success} 실패={fail} ===")

    # ── 2단계: description 업데이트 (개별 처리 - 이미지 URL 필요) ──
    logger.info("\n=== 2단계: description 업데이트 시작 ===")

    db = sqlite3.connect(os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    ))
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute(
        "SELECT wc_product_id FROM products WHERE wc_product_id IS NOT NULL"
    )
    all_ids = [row["wc_product_id"] for row in cur.fetchall()]
    db.close()

    desc_success = 0
    desc_fail = 0

    for idx, wc_id in enumerate(all_ids):
        try:
            # 기존 상품 이미지 URL 가져오기
            resp = api.get(f"products/{wc_id}")
            if resp.status_code != 200:
                desc_fail += 1
                continue

            product_data = resp.json()
            existing_images = product_data.get("images", [])
            photo_urls = [img["src"] for img in existing_images if img.get("src")]

            # description 재구성
            new_desc = build_description(notice_url, photo_urls)

            # 이미지는 첫 장만 남기기 (목록 썸네일)
            new_images = []
            if photo_urls:
                new_images = [{"src": photo_urls[0], "position": 0}]

            # 업데이트
            update_resp = api.put(f"products/{wc_id}", {
                "description": new_desc,
                "images": new_images,
            })

            if update_resp.status_code == 200:
                desc_success += 1
            else:
                desc_fail += 1
                logger.error(f"  description 실패 ID={wc_id}: {update_resp.status_code}")

        except Exception as e:
            desc_fail += 1
            logger.error(f"  description 에러 ID={wc_id}: {e}")

        if (idx + 1) % 20 == 0:
            logger.info(f"  [{idx + 1}/{len(all_ids)}] desc 성공={desc_success} 실패={desc_fail}")
            time.sleep(3)
        else:
            time.sleep(1)

    logger.info(f"\n=== 2단계 완료 (description): 성공={desc_success} 실패={desc_fail} ===")
    logger.info(f"\n=== 전체 완료 ===")
    logger.info(f"  상품명+카테고리: 성공={success} 실패={fail}")
    logger.info(f"  description: 성공={desc_success} 실패={desc_fail}")


if __name__ == "__main__":
    main()
