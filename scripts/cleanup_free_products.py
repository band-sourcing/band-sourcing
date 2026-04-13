#!/usr/bin/env python3
"""
[Step 4 FIX-E] 기존 등록된 FREE 사이즈 상품 삭제.

의류천국22 소스 상품 중 FREE 사이즈 해당 상품을 WC + DB에서 제거.
DB에 sizes 컬럼이 없으므로 WC description에서 FREE 키워드를 탐지.

사용법:
  cd /opt/band-sourcing
  source venv/bin/activate
  python3 scripts/cleanup_free_products.py [--dry-run]

  --dry-run: 실제 삭제 없이 대상만 출력
"""

import sqlite3
import time
import logging
import sys
import os
import re
import argparse
from dotenv import load_dotenv
from woocommerce import API

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("cleanup_free")

load_dotenv()

# 의류천국22 밴드 키
_CLOTHING_BAND_KEY = "97874425"

# FREE 사이즈 감지 패턴
_FREE_PATTERNS = [
    re.compile(r'사이즈\s*[-:]?\s*(?:남여공용\s*)?FREE', re.IGNORECASE),
    re.compile(r'사이즈\s*[-:]?\s*프리', re.IGNORECASE),
    re.compile(r'사이즈\s*[-:]?\s*F\b', re.IGNORECASE),
]


def has_free_size_in_description(desc: str) -> bool:
    """WC description HTML에서 FREE 사이즈 키워드 탐지."""
    if not desc:
        return False
    # HTML 태그 제거 후 검사
    text = re.sub(r'<[^>]+>', ' ', desc)
    for pat in _FREE_PATTERNS:
        if pat.search(text):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="FREE 사이즈 상품 삭제")
    parser.add_argument("--dry-run", action="store_true", help="실제 삭제 없이 대상만 출력")
    args = parser.parse_args()

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

    # 의류천국22 상품만 조회
    cur.execute(
        "SELECT id, wc_product_id, brand_tag, product_name "
        "FROM products WHERE band_key = ? AND wc_product_id IS NOT NULL",
        (_CLOTHING_BAND_KEY,)
    )
    rows = cur.fetchall()
    logger.info(f"의류천국22 상품: {len(rows)}개 → FREE 사이즈 검사 시작")

    targets = []

    for idx, row in enumerate(rows):
        wc_id = row["wc_product_id"]
        try:
            resp = api.get(f"products/{wc_id}")
            if resp.status_code != 200:
                continue
            data = resp.json()
            desc = data.get("description", "")

            if has_free_size_in_description(desc):
                targets.append({
                    "db_id": row["id"],
                    "wc_id": wc_id,
                    "brand": row["brand_tag"],
                    "name": row["product_name"],
                })
                logger.info(
                    f"  FREE 감지: {row['brand_tag']} {row['product_name']} (WC#{wc_id})"
                )
        except Exception as e:
            logger.error(f"  조회 실패 WC#{wc_id}: {e}")

        # rate limit
        if (idx + 1) % 10 == 0:
            time.sleep(1)
            if (idx + 1) % 50 == 0:
                logger.info(f"  진행: {idx+1}/{len(rows)}")

    logger.info(f"\nFREE 사이즈 상품: {len(targets)}건 감지")

    if args.dry_run:
        logger.info("(dry-run 모드 - 실제 삭제 없음)")
        for t in targets:
            logger.info(f"  [삭제대상] {t['brand']} {t['name']} (WC#{t['wc_id']})")
        db.close()
        return

    # 실제 삭제
    deleted = 0
    failed = 0
    for t in targets:
        try:
            # WC에서 삭제 (force=True → 완전삭제 / False → 휴지통)
            resp = api.delete(f"products/{t['wc_id']}", params={"force": True})
            if resp.status_code == 200:
                # DB에서도 삭제
                cur.execute("DELETE FROM products WHERE id = ?", (t["db_id"],))
                deleted += 1
                logger.info(f"  삭제 완료: {t['brand']} {t['name']}")
            else:
                failed += 1
                logger.error(f"  WC 삭제 실패: {t['brand']} {t['name']} ({resp.status_code})")
        except Exception as e:
            failed += 1
            logger.error(f"  삭제 에러: {t['brand']} {t['name']} ({e})")

        time.sleep(0.5)

    db.commit()
    db.close()

    logger.info(f"\n=== FREE 상품 삭제 완료 ===")
    logger.info(f"  삭제: {deleted}건 / 실패: {failed}건")


if __name__ == "__main__":
    main()
