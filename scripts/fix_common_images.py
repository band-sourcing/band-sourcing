#!/usr/bin/env python3
"""
WC 상품 description에서 밴드 공통 이미지 제거 + 썸네일 교체.

제거 대상:
  1. 밴드 프로필 이미지: a_h9hUd018svc19uvfzsdn00w9_4k8958.jpg
  2. 밴드 공통 이미지: _5ksoqj.png 패턴
  3. 밴드 하단 고정 이미지: _fwc5at 패턴 6개

썸네일 교체:
  - 기존: [1]번(고양이) -> 실제 상품 이미지 첫 번째로 교체

사용법:
  cd /opt/band-sourcing
  python3 scripts/fix_common_images.py --dry-run    # 미리보기
  python3 scripts/fix_common_images.py              # 실행
"""

import argparse
import logging
import os
import re
import sys
import time

from dotenv import load_dotenv
from woocommerce import API

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fix_common_images")

load_dotenv()

# ── 제거 대상 패턴 ──

# 밴드 프로필 이미지 (고양이)
PROFILE_IMG = "a_h9hUd018svc19uvfzsdn00w9_4k8958"

# 밴드 공통 이미지 (_5ksoqj.png 패턴)
COMMON_PNG_PATTERN = "_5ksoqj"

# 밴드 하단 고정 이미지 6개 (URL 전체)
FOOTER_URLS = {
    "https://coresos-phinf.pstatic.net/a/3978c6/3_c94Ud018svcuglxur2ti9xu_fwc5at.jpg",
    "https://coresos-phinf.pstatic.net/a/39784a/3_b94Ud018svct4ncqwo9v77y_fwc5at.jpg",
    "https://coresos-phinf.pstatic.net/a/397807/3_a94Ud018svcafq130uomry2_fwc5at.jpg",
    "https://coresos-phinf.pstatic.net/a/3978f6/3_894Ud018svc1hofcwsdwnyjf_fwc5at.jpg",
    "https://coresos-phinf.pstatic.net/a/3978aa/3_794Ud018svc1jlqptz5ydktb_fwc5at.jpg",
    "https://coresos-phinf.pstatic.net/a/397873/3_694Ud018svc1k59mta8uohbe_fwc5at.jpg",
}

# img 태그 매칭 패턴
IMG_TAG_RE = re.compile(r'<img\s[^>]*src="([^"]+)"[^>]*/?\s*>', re.IGNORECASE)


def should_remove_img(src: str) -> bool:
    """이 이미지 URL이 제거 대상인지 판별."""
    if PROFILE_IMG in src:
        return True
    if COMMON_PNG_PATTERN in src:
        return True
    if src in FOOTER_URLS:
        return True
    # 하단 고정 이미지 파일명 매칭 (URL 전체가 다를 수 있으므로 파일명으로)
    FOOTER_FILE_IDS = {
        "3_c94Ud018svcuglxur2ti9xu_fwc5at",
        "3_b94Ud018svct4ncqwo9v77y_fwc5at",
        "3_a94Ud018svcafq130uomry2_fwc5at",
        "3_894Ud018svc1hofcwsdwnyjf_fwc5at",
        "3_794Ud018svc1jlqptz5ydktb_fwc5at",
        "3_694Ud018svc1k59mta8uohbe_fwc5at",
    }
    if any(fid in src for fid in FOOTER_FILE_IDS):
        return True
    return False


def clean_description(desc: str) -> tuple[str, int]:
    """
    description HTML에서 공통 이미지 img 태그 제거.
    Returns: (cleaned_desc, removed_count)
    """
    removed = 0

    def replacer(match):
        nonlocal removed
        src = match.group(1)
        if should_remove_img(src):
            removed += 1
            return ""
        return match.group(0)

    cleaned = IMG_TAG_RE.sub(replacer, desc)
    # 빈 줄 정리
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = cleaned.strip()
    return cleaned, removed


def find_first_product_image(desc: str) -> str | None:
    """description에서 공통 이미지/공지 배너 제외한 첫 번째 상품 이미지 URL 추출."""
    for match in IMG_TAG_RE.finditer(desc):
        src = match.group(1)
        # 공지 배너 스킵
        if "notice-banner" in src:
            continue
        # 공통 이미지 스킵
        if should_remove_img(src):
            continue
        return src
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="실제 업데이트 없이 미리보기")
    args = parser.parse_args()

    api = API(
        url=os.getenv("WC_SITE_URL"),
        consumer_key=os.getenv("WC_CONSUMER_KEY"),
        consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
        version="wc/v3",
        timeout=120,
    )

    # 전체 상품 페이지네이션으로 조회
    page = 1
    per_page = 100
    total_processed = 0
    desc_fixed = 0
    thumb_fixed = 0
    errors = 0
    skipped = 0

    while True:
        logger.info(f"페이지 {page} 조회 중...")
        resp = api.get(
            "products",
            params={
                "per_page": per_page,
                "page": page,
                "_fields": "id,name,description,images",
            },
        )

        if resp.status_code != 200:
            logger.error(f"WC API 에러: {resp.status_code}")
            break

        products = resp.json()
        if not products:
            break

        for p in products:
            wc_id = p["id"]
            name = p.get("name", "")[:40]
            desc = p.get("description", "")
            current_images = p.get("images", [])

            # 공통 이미지가 있는지 확인
            has_common = any(
                should_remove_img(m.group(1))
                for m in IMG_TAG_RE.finditer(desc)
            )

            if not has_common:
                skipped += 1
                continue

            # description 정리
            cleaned_desc, removed_count = clean_description(desc)

            # 썸네일 교체: 현재 썸네일이 고양이인지 확인
            new_thumb_url = None
            if current_images:
                current_thumb = current_images[0].get("src", "")
                if PROFILE_IMG in current_thumb:
                    # 실제 상품 이미지로 교체
                    new_thumb_url = find_first_product_image(desc)

            total_processed += 1

            if args.dry_run:
                logger.info(
                    f"  [DRY] ID={wc_id} {name} -> "
                    f"img제거={removed_count}개 / 썸네일교체={'O' if new_thumb_url else 'X'}"
                )
                if removed_count > 0:
                    desc_fixed += 1
                if new_thumb_url:
                    thumb_fixed += 1
                continue

            # 실제 업데이트
            payload = {"description": cleaned_desc}
            if new_thumb_url:
                payload["images"] = [{"src": new_thumb_url, "position": 0}]

            try:
                resp_put = api.put(f"products/{wc_id}", payload)
                if resp_put.status_code == 200:
                    if removed_count > 0:
                        desc_fixed += 1
                    if new_thumb_url:
                        thumb_fixed += 1
                    logger.info(
                        f"  ✅ ID={wc_id} {name} -> "
                        f"img제거={removed_count} / 썸네일={'교체' if new_thumb_url else '유지'}"
                    )
                else:
                    errors += 1
                    logger.error(f"  ❌ ID={wc_id} 업데이트 실패: {resp_put.status_code}")
            except Exception as e:
                errors += 1
                logger.error(f"  ❌ ID={wc_id} 에러: {e}")

            time.sleep(0.5)

        page += 1

    logger.info("")
    logger.info("=" * 50)
    prefix = "[DRY-RUN] " if args.dry_run else ""
    logger.info(f"{prefix}완료 리포트")
    logger.info(f"  대상 상품: {total_processed}개")
    logger.info(f"  description 수정: {desc_fixed}개")
    logger.info(f"  썸네일 교체: {thumb_fixed}개")
    logger.info(f"  스킵 (공통이미지 없음): {skipped}개")
    logger.info(f"  에러: {errors}개")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
