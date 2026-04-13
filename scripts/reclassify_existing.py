#!/usr/bin/env python3
"""
기존 WC 상품 카테고리 재분류 스크립트.

기존 bag_watch/outer/etc 3종 → bag/watch/outer/top/bottom/accessory/golf/etc 8종
+ 성별 분류 (사이즈 기반) → WC 남성/여성 하위 카테고리 매핑

사용법:
  cd /opt/band-sourcing
  source venv/bin/activate
  python3 scripts/reclassify_existing.py [--dry-run]

  --dry-run: 실제 WC 업데이트 없이 분류 결과만 출력
"""

import sqlite3
import time
import logging
import sys
import os
import argparse
import re
import yaml
from dotenv import load_dotenv
from woocommerce import API

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.margin_engine import classify_category, classify_gender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("reclassify")

load_dotenv()


def load_settings():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_wc_category(category: str, gender: str, wc_cat_config: dict) -> int:
    """세분화 카테고리 + 성별 → WC 카테고리 ID."""
    # 성별 무관 카테고리
    if category == "bag":
        return wc_cat_config.get("bag", 85)
    elif category == "watch":
        return wc_cat_config.get("watch", 86)
    elif category == "accessory":
        return wc_cat_config.get("accessory", 89)

    # 성별 기반 카테고리
    if category in ("outer", "top", "bottom", "golf"):
        gender_conf = wc_cat_config.get(gender, {})
        if isinstance(gender_conf, dict):
            cat_id = gender_conf.get(category)
            if cat_id:
                return cat_id

    return wc_cat_config.get("etc", 89)


def extract_sizes_from_wc(api, wc_product_id: int) -> list[str]:
    """WC 상품의 description에서 사이즈 정보 추출 시도."""
    try:
        resp = api.get(f"products/{wc_product_id}")
        if resp.status_code != 200:
            return []
        data = resp.json()
        desc = data.get("description", "")

        # 사이즈 패턴 추출
        size_pattern = re.compile(r'사이즈\s*[-:]\s*(.+?)(?:<br|<\/|$)')
        m = size_pattern.search(desc)
        if m:
            raw = m.group(1)
            sizes = [s.strip() for s in re.split(r'[,/\s]+', raw) if s.strip()]
            return sizes
        return []
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(description="기존 WC 상품 카테고리 재분류")
    parser.add_argument("--dry-run", action="store_true", help="실제 업데이트 없이 결과만 출력")
    args = parser.parse_args()

    settings = load_settings()
    category_keywords = settings.get("category_keywords", {})
    golf_brand_tags = settings.get("golf_brand_tags", [])
    gender_config = settings.get("gender_classification", {})
    wc_cat_config = settings.get("wc_categories", {})

    api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=120
    )

    # DB에서 기존 상품 조회
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    )
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute(
        "SELECT id, wc_product_id, brand_tag, product_name, category "
        "FROM products WHERE wc_product_id IS NOT NULL"
    )
    rows = cur.fetchall()

    total = len(rows)
    logger.info(f"재분류 대상: {total}개 (dry-run={args.dry_run})")

    stats = {"updated": 0, "unchanged": 0, "failed": 0}
    batch = []
    batch_db_updates = []

    for idx, row in enumerate(rows):
        wc_id = row["wc_product_id"]
        brand_tag = row["brand_tag"]
        product_name = row["product_name"]
        old_category = row["category"]
        db_id = row["id"]

        # 새 카테고리 분류
        new_category = classify_category(
            product_name, "", category_keywords,
            brand_tag=brand_tag, golf_brand_tags=golf_brand_tags,
        )

        # 성별 분류 (키워드 + WC description에서 사이즈 추출)
        sizes = extract_sizes_from_wc(api, wc_id)
        gender = classify_gender(sizes, gender_config, product_name=product_name)

        # WC 카테고리 ID
        new_wc_cat_id = resolve_wc_category(new_category, gender, wc_cat_config)

        if args.dry_run:
            if old_category != new_category:
                logger.info(
                    f"  [{idx+1}/{total}] {brand_tag} {product_name[:30]} | "
                    f"{old_category} → {new_category} ({gender}) | WC cat={new_wc_cat_id}"
                )
            # 사이즈 추출 rate limit
            if (idx + 1) % 20 == 0:
                time.sleep(1)
            continue

        batch.append({
            "id": wc_id,
            "categories": [{"id": new_wc_cat_id}],
        })
        batch_db_updates.append((new_category, db_id))

        # 10개씩 배치 업데이트
        if len(batch) >= 10:
            try:
                resp = api.post("products/batch", {"update": batch})
                if resp.status_code == 200:
                    stats["updated"] += len(batch)
                    # DB도 업데이트
                    for cat, did in batch_db_updates:
                        cur.execute(
                            "UPDATE products SET category = ? WHERE id = ?",
                            (cat, did)
                        )
                    db.commit()
                else:
                    stats["failed"] += len(batch)
                    logger.error(f"배치 실패: {resp.status_code}")
            except Exception as e:
                stats["failed"] += len(batch)
                logger.error(f"배치 에러: {e}")

            batch = []
            batch_db_updates = []
            logger.info(f"  [{idx+1}/{total}] 업데이트={stats['updated']} 실패={stats['failed']}")
            time.sleep(2)

    # 남은 배치 처리
    if batch and not args.dry_run:
        try:
            resp = api.post("products/batch", {"update": batch})
            if resp.status_code == 200:
                stats["updated"] += len(batch)
                for cat, did in batch_db_updates:
                    cur.execute(
                        "UPDATE products SET category = ? WHERE id = ?",
                        (cat, did)
                    )
                db.commit()
            else:
                stats["failed"] += len(batch)
        except Exception as e:
            stats["failed"] += len(batch)
            logger.error(f"남은 배치 에러: {e}")

    db.close()

    logger.info(f"\n=== 재분류 완료 ===")
    if args.dry_run:
        logger.info("  (dry-run 모드 - 실제 변경 없음)")
    else:
        logger.info(f"  업데이트={stats['updated']} 실패={stats['failed']}")


if __name__ == "__main__":
    main()
