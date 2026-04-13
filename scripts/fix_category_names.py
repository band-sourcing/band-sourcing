#!/usr/bin/env python3
"""
[Step 4 FIX-G] WC 카테고리명 정리.

고객 사이트 메뉴 구조:
  공지사항 / 시계 / 가방 / 악세사리 / 지갑 / 신발
  남자 > 아우터 / 상의 / 하의
  여자 > 아우터 / 상의 / 하의

현재 문제:
  - "악세사리/잡화" → "악세사리"로 변경 필요
  - 기타 카테고리명도 고객 메뉴와 일치하는지 확인

사용법:
  cd /opt/band-sourcing
  source venv/bin/activate
  python3 scripts/fix_category_names.py [--dry-run]
"""

import logging
import sys
import os
import argparse
from dotenv import load_dotenv
from woocommerce import API

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("fix_cat_names")

load_dotenv()

# 고객 확정 카테고리명 (ID: 원하는 이름)
EXPECTED_NAMES = {
    74: "남자",
    75: "여자",
    77: "아우터",   # parent=75(여자)
    78: "아우터",   # parent=74(남자)
    79: "상의",     # parent=74(남자)
    80: "상의",     # parent=75(여자)
    81: "하의",     # parent=75(여자)
    82: "하의",     # parent=74(남자)
    83: "지갑",
    84: "신발",
    85: "가방",
    86: "시계",
    89: "악세사리",  # 현재 "악세사리/잡화"일 수 있음
    31: "미분류",    # etc → 미분류
}


def main():
    parser = argparse.ArgumentParser(description="WC 카테고리명 정리")
    parser.add_argument("--dry-run", action="store_true", help="실제 변경 없이 확인만")
    args = parser.parse_args()

    api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=30
    )

    # 현재 카테고리 조회
    resp = api.get("products/categories", params={"per_page": 100})
    if resp.status_code != 200:
        logger.error(f"카테고리 조회 실패: {resp.status_code}")
        return

    categories = resp.json()
    logger.info(f"현재 WC 카테고리: {len(categories)}개\n")

    changes = []
    for cat in sorted(categories, key=lambda x: x["id"]):
        cat_id = cat["id"]
        current_name = cat["name"]
        parent = cat["parent"]

        if cat_id in EXPECTED_NAMES:
            expected = EXPECTED_NAMES[cat_id]
            if current_name != expected:
                changes.append({
                    "id": cat_id,
                    "current": current_name,
                    "expected": expected,
                    "parent": parent,
                })
                logger.info(
                    f"  ⚠ ID {cat_id}: \"{current_name}\" → \"{expected}\" (parent={parent})"
                )
            else:
                logger.info(
                    f"  ✓ ID {cat_id}: \"{current_name}\" (OK)"
                )
        else:
            logger.info(
                f"  - ID {cat_id}: \"{current_name}\" (관리 외 / parent={parent})"
            )

    if not changes:
        logger.info("\n모든 카테고리명이 정상입니다!")
        return

    logger.info(f"\n변경 필요: {len(changes)}건")

    if args.dry_run:
        logger.info("(dry-run 모드 - 실제 변경 없음)")
        return

    # 실제 변경
    for ch in changes:
        try:
            resp = api.put(
                f"products/categories/{ch['id']}",
                {"name": ch["expected"]}
            )
            if resp.status_code == 200:
                logger.info(f"  ✓ 변경 완료: ID {ch['id']} → \"{ch['expected']}\"")
            else:
                logger.error(f"  ✗ 변경 실패: ID {ch['id']} ({resp.status_code})")
        except Exception as e:
            logger.error(f"  ✗ 에러: ID {ch['id']} ({e})")

    logger.info("\n=== 카테고리명 정리 완료 ===")


if __name__ == "__main__":
    main()
