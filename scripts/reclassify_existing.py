#!/usr/bin/env python3
"""
기존 WC 상품 카테고리 재분류 + 마진 재계산 스크립트.

v2: golf 삭제 / wallet·shoes 추가 / 마진 50000/40000/30000
+ 가격 재계산 (새 마진 적용)

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

from src.margin_engine import classify_category, calculate_sell_price

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
    """카테고리 → WC 카테고리 ID (성별 구분 제거)."""
    return wc_cat_config.get(category, wc_cat_config.get("etc", 89))


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
    parser = argparse.ArgumentParser(description="기존 WC 상품 카테고리 재분류 + 마진 재계산")
    parser.add_argument("--dry-run", action="store_true", help="실제 업데이트 없이 결과만 출력")
    args = parser.parse_args()

    settings = load_settings()
    category_keywords = settings.get("category_keywords", {})
    wc_cat_config = settings.get("wc_categories", {})
    margin_config = settings.get("margin", {})
    keyword_exclusions = settings.get("keyword_exclusions", {})

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
        "SELECT id, wc_product_id, brand_tag, product_name, category, cost_price, sell_price, margin_applied "
        "FROM products WHERE wc_product_id IS NOT NULL"
    )
    rows = cur.fetchall()

    total = len(rows)
    logger.info(f"재분류 대상: {total}개 (dry-run={args.dry_run})")

    stats = {"cat_changed": 0, "price_changed": 0, "unchanged": 0, "failed": 0}
    cat_counts = {}
    batch = []
    batch_db_updates = []

    for idx, row in enumerate(rows):
        wc_id = row["wc_product_id"]
        brand_tag = row["brand_tag"]
        product_name = row["product_name"] or ""
        old_category = row["category"]
        cost_price = row["cost_price"] or 0
        old_sell = row["sell_price"] or 0
        db_id = row["id"]

        # 새 카테고리 분류 (golf_brand_tags 제거됨 — 하위 호환 파라미터만 유지)
        new_category = classify_category(
            product_name, "", category_keywords,
            brand_tag=brand_tag,
            keyword_exclusions=keyword_exclusions,
        )

        # 새 마진 계산
        new_sell, new_margin = calculate_sell_price(cost_price, new_category, margin_config)

        # 성별 분류 제거 — gender는 하위 호환용 "male" 고정
        gender = "male"

        # WC 카테고리 ID
        new_wc_cat_id = resolve_wc_category(new_category, gender, wc_cat_config)

        # 카운트
        cat_counts[new_category] = cat_counts.get(new_category, 0) + 1

        cat_changed = old_category != new_category
        price_changed = old_sell != new_sell

        if args.dry_run:
            if cat_changed or price_changed:
                changes = []
                if cat_changed:
                    changes.append(f"cat: {old_category}→{new_category}")
                if price_changed:
                    changes.append(f"price: {old_sell:,}→{new_sell:,}")
                logger.info(
                    f"  [{idx+1}/{total}] {brand_tag} {product_name[:30]} | "
                    f"{' | '.join(changes)} | WC cat={new_wc_cat_id}"
                )
                if cat_changed:
                    stats["cat_changed"] += 1
                if price_changed:
                    stats["price_changed"] += 1
            else:
                stats["unchanged"] += 1
            # rate limit
            if (idx + 1) % 20 == 0:
                time.sleep(1)
            continue

        # 실제 업데이트 준비
        update_data = {"id": wc_id, "categories": [{"id": new_wc_cat_id}]}
        if price_changed:
            update_data["regular_price"] = str(new_sell)

        batch.append(update_data)
        batch_db_updates.append((new_category, cost_price, new_sell, new_margin, db_id))

        # 10개씩 배치 업데이트
        if len(batch) >= 10:
            _flush_batch(api, db, cur, batch, batch_db_updates, stats, idx, total)
            batch = []
            batch_db_updates = []
            time.sleep(2)

    # 남은 배치 처리
    if batch and not args.dry_run:
        _flush_batch(api, db, cur, batch, batch_db_updates, stats, total - 1, total)

    db.close()

    logger.info(f"\n=== 재분류 완료 ===")
    if args.dry_run:
        logger.info("  (dry-run 모드 - 실제 변경 없음)")
    logger.info(f"  카테고리 변경={stats['cat_changed']} 가격 변경={stats['price_changed']} "
                f"변경없음={stats['unchanged']} 실패={stats['failed']}")
    logger.info(f"  카테고리별 분포:")
    for cat, cnt in sorted(cat_counts.items()):
        logger.info(f"    {cat}: {cnt}건")


def _flush_batch(api, db, cur, batch, batch_db_updates, stats, idx, total):
    """배치 WC 업데이트 + DB 업데이트."""
    try:
        resp = api.post("products/batch", {"update": batch})
        if resp.status_code == 200:
            stats["cat_changed"] += len(batch)
            for cat, cost, sell, margin, did in batch_db_updates:
                cur.execute(
                    "UPDATE products SET category = ?, cost_price = ?, sell_price = ?, margin_applied = ? WHERE id = ?",
                    (cat, cost, sell, margin, did)
                )
            db.commit()
        else:
            stats["failed"] += len(batch)
            logger.error(f"배치 실패: {resp.status_code}")
    except Exception as e:
        stats["failed"] += len(batch)
        logger.error(f"배치 에러: {e}")

    logger.info(f"  [{idx+1}/{total}] 업데이트={stats['cat_changed']} 실패={stats['failed']}")


if __name__ == "__main__":
    main()
