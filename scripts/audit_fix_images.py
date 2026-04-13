#!/usr/bin/env python3
"""
WC 상품 이미지/description 통합 검증 + 보정.

전체 흐름:
  1. DB에서 모든 WC 상품의 wc_product_id + post_key 로드
  2. WC API로 각 상품의 현재 상태 조회 (썸네일 / description)
  3. 문제 있는 상품 분류:
     A) 고양이 썸네일 (공통 프로필 이미지)
     B) 썸네일 없음 (images 비어있음)
     C) description에 공통 이미지 잔존
  4. 문제 상품 → 밴드 게시글 직접 방문 → 상품 이미지 추출 → WC 업데이트

사용법:
  cd /opt/band-sourcing
  python3 scripts/audit_fix_images.py --dry-run    # 문제 진단만
  python3 scripts/audit_fix_images.py              # 진단 + 수정
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
from src.content_parser import preprocess_content, _clean_raw_content

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("audit_fix")

load_dotenv()

# ── 공통 이미지 패턴 ──

PROFILE_IMG = "a_h9hUd018svc19uvfzsdn00w9_4k8958"
COMMON_PNG = "_5ksoqj"

FOOTER_FILE_IDS = {
    "3_c94Ud018svcuglxur2ti9xu_fwc5at",
    "3_b94Ud018svct4ncqwo9v77y_fwc5at",
    "3_a94Ud018svcafq130uomry2_fwc5at",
    "3_894Ud018svc1hofcwsdwnyjf_fwc5at",
    "3_794Ud018svc1jlqptz5ydktb_fwc5at",
    "3_694Ud018svc1k59mta8uohbe_fwc5at",
}

SKIP_IMG_PATTERNS = [
    PROFILE_IMG,
    COMMON_PNG,
    "profile", "avatar", "icon", "emoji",
    "sticker", "emoticon", "gif_origin",
    "logo", "1x1",
]

IMG_TAG_RE = re.compile(r'<img\s[^>]*src="([^"]+)"[^>]*/?\s*>', re.IGNORECASE)
MD5_PATTERN = re.compile(r'^[0-9a-f]{16}$', re.IGNORECASE)


def should_skip_img(url):
    low = url.lower()
    if any(p in low for p in SKIP_IMG_PATTERNS):
        return True
    if any(fid in url for fid in FOOTER_FILE_IDS):
        return True
    return False


def should_remove_from_desc(src):
    """description에서 제거해야 할 img인지 판별."""
    if PROFILE_IMG in src:
        return True
    if COMMON_PNG in src:
        return True
    if any(fid in src for fid in FOOTER_FILE_IDS):
        return True
    return False


def clean_description(desc):
    """description HTML에서 공통 이미지 img 태그 제거."""
    removed = 0

    def replacer(match):
        nonlocal removed
        src = match.group(1)
        if should_remove_from_desc(src):
            removed += 1
            return ""
        return match.group(0)

    cleaned = IMG_TAG_RE.sub(replacer, desc)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip(), removed


def find_first_product_image_in_desc(desc):
    """description에서 공통/공지 이미지 제외한 첫 번째 상품 이미지 URL."""
    for match in IMG_TAG_RE.finditer(desc):
        src = match.group(1)
        if "notice-banner" in src:
            continue
        if should_remove_from_desc(src):
            continue
        return src
    return None


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
        import httpx
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


# ── 밴드 게시글 이미지 추출 ──


def extract_from_band(page, band_key, post_id, max_retries=3):
    """
    밴드 게시글 직접 방문 -> 본문 텍스트 + 상품 이미지 추출.
    Returns: (content_text, photo_urls)
    """
    detail_url = f"https://band.us/band/{band_key}/post/{post_id}"

    for attempt in range(max_retries):
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            # 더보기 클릭
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

            # 본문 텍스트
            content = ""
            text_selectors = [
                "p.txtBody", ".postText", "._postText",
                '[class*="postText"]', '[class*="txtBody"]',
                ".dPostBody", "._postBody",
            ]
            for sel in text_selectors:
                text_el = page.locator(sel).first
                if text_el.count() > 0:
                    text = text_el.inner_text().strip()
                    if text and len(text) > 3:
                        content = text
                        break

            # 상품 이미지: .collageItem img (DOM 구조 기반)
            photo_urls = []
            seen_urls = set()

            img_elements = page.locator(".collageItem img, .collageImage._postMediaItem img")
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

            # fallback: img._image
            if not photo_urls:
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

            return content, photo_urls

        except Exception as e:
            logger.warning(f"  접근 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(3)

    return None, []


# ── 메인 ──


def main():
    parser = argparse.ArgumentParser(description="WC 상품 이미지/description 통합 검증 + 보정")
    parser.add_argument("--dry-run", action="store_true", help="진단만 (수정 안 함)")
    args = parser.parse_args()

    settings = load_settings()
    notice_url = validate_notice_url(
        settings.get("images", {}).get("notice_url", "")
    )

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

    # ── Phase 1: 전체 WC 상품 스캔 + 문제 분류 ──
    logger.info("=" * 60)
    logger.info("[Phase 1] 전체 WC 상품 스캔")

    issues = {
        "cat_thumbnail": [],      # 고양이 썸네일
        "no_thumbnail": [],       # 썸네일 없음
        "desc_common_img": [],    # description에 공통이미지 잔존
        "no_desc_text": [],       # description에 본문 텍스트 없음
    }
    total_products = 0
    ok_count = 0

    page = 1
    while True:
        resp = wc_api.get(
            "products",
            params={"per_page": 100, "page": page, "_fields": "id,name,images,description"},
        )
        if resp.status_code != 200:
            logger.error(f"WC API 에러: {resp.status_code}")
            break
        products = resp.json()
        if not products:
            break

        for p in products:
            total_products += 1
            wc_id = p["id"]
            name = p.get("name", "")
            imgs = p.get("images", [])
            desc = p.get("description", "")

            has_issue = False

            # 고양이 썸네일
            if imgs and PROFILE_IMG in imgs[0].get("src", ""):
                issues["cat_thumbnail"].append(wc_id)
                has_issue = True

            # 썸네일 없음
            if not imgs:
                issues["no_thumbnail"].append(wc_id)
                has_issue = True

            # description에 공통이미지
            if PROFILE_IMG in desc or COMMON_PNG in desc:
                issues["desc_common_img"].append(wc_id)
                has_issue = True

            # description에 본문 텍스트 없음
            if '<div style="margin-bottom:20px; line-height:1.8' not in desc:
                issues["no_desc_text"].append(wc_id)
                has_issue = True

            if not has_issue:
                ok_count += 1

        page += 1

    # 문제 상품 = 밴드 재추출 필요한 것들 (중복 제거)
    need_band_fix = set()
    for key in issues:
        need_band_fix.update(issues[key])

    logger.info(f"  전체 상품: {total_products}개")
    logger.info(f"  정상: {ok_count}개")
    logger.info(f"  고양이 썸네일: {len(issues['cat_thumbnail'])}개")
    logger.info(f"  썸네일 없음: {len(issues['no_thumbnail'])}개")
    logger.info(f"  description 공통이미지: {len(issues['desc_common_img'])}개")
    logger.info(f"  description 본문 없음: {len(issues['no_desc_text'])}개")
    logger.info(f"  밴드 재추출 필요: {len(need_band_fix)}개")

    if not need_band_fix:
        logger.info("모든 상품 정상 -> 종료")
        return

    if args.dry_run:
        logger.info("[DRY-RUN] 진단 완료 -> 종료")
        return

    # ── Phase 2: DB에서 post_key 매핑 ──
    logger.info("=" * 60)
    logger.info("[Phase 2] DB post_key 매핑")

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    targets = []
    db_miss = 0
    md5_skip = 0

    for wc_id in need_band_fix:
        cur = db.cursor()
        cur.execute(
            "SELECT post_key, band_key FROM products WHERE wc_product_id = ?",
            (wc_id,),
        )
        row = cur.fetchone()
        if not row:
            db_miss += 1
            continue

        post_key = row["post_key"]
        parts = post_key.split("_", 1)
        if len(parts) != 2:
            md5_skip += 1
            continue

        band_key, post_id = parts
        if MD5_PATTERN.match(post_id):
            md5_skip += 1
            continue

        targets.append({
            "wc_id": wc_id,
            "band_key": band_key,
            "post_id": post_id,
            "issues": [k for k, v in issues.items() if wc_id in v],
        })

    db.close()

    logger.info(f"  밴드 접근 대상: {len(targets)}개")
    logger.info(f"  DB 매칭 실패: {db_miss}개")
    logger.info(f"  md5 스킵: {md5_skip}개")

    if not targets:
        logger.info("처리할 대상 없음 -> 종료")
        return

    # ── Phase 3: 밴드 접근 + WC 업데이트 ──
    logger.info("=" * 60)
    logger.info("[Phase 3] 밴드 접근 + WC 업데이트")

    scraper = BandScraper(
        naver_id=os.getenv("NAVER_ID", ""),
        naver_pw=os.getenv("NAVER_PW", ""),
        cutoff_date=settings["band"]["cutoff_date"],
        session_path=os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "band_session.json"
        ),
    )

    stats = {
        "thumb_fixed": 0,
        "desc_fixed": 0,
        "band_no_image": 0,
        "band_no_content": 0,
        "errors": 0,
    }

    try:
        scraper.ensure_logged_in()
        browser_page = scraper._page

        for idx, t in enumerate(targets):
            wc_id = t["wc_id"]
            band_key = t["band_key"]
            post_id = t["post_id"]
            issue_types = t["issues"]

            logger.info(
                f"  [{idx + 1}/{len(targets)}] wc_id={wc_id} "
                f"issues={issue_types}"
            )

            # 밴드 게시글 방문
            content, photo_urls = extract_from_band(
                browser_page, band_key, post_id
            )

            # 업데이트 payload 구성
            payload = {}

            # 썸네일 수정 (고양이 or 없음)
            if "cat_thumbnail" in issue_types or "no_thumbnail" in issue_types:
                if photo_urls:
                    payload["images"] = [{"src": photo_urls[0], "position": 0}]
                    stats["thumb_fixed"] += 1
                else:
                    # 이미지 없으면 고양이 제거만
                    if "cat_thumbnail" in issue_types:
                        payload["images"] = []
                    stats["band_no_image"] += 1

            # description 수정
            needs_desc_fix = (
                "desc_common_img" in issue_types or
                "no_desc_text" in issue_types
            )

            if needs_desc_fix:
                # 현재 description 가져오기
                resp_get = wc_api.get(
                    f"products/{wc_id}", params={"_fields": "description"}
                )
                current_desc = ""
                if resp_get.status_code == 200:
                    current_desc = resp_get.json().get("description", "")

                # 공통 이미지 제거
                cleaned_desc, _ = clean_description(current_desc)

                # 본문 텍스트 없으면 밴드에서 가져온 것으로 재구성
                if "no_desc_text" in issue_types and content:
                    cleaned_content = preprocess_content(content)
                    raw_content = _clean_raw_content(cleaned_content)
                    if raw_content:
                        cleaned_desc = build_description(
                            notice_url, raw_content, photo_urls
                        )
                        stats["desc_fixed"] += 1
                elif "desc_common_img" in issue_types:
                    # 공통 이미지만 제거
                    stats["desc_fixed"] += 1

                # 이미지 없는 상품에 밴드 이미지 추가
                if photo_urls and not any(
                    u in cleaned_desc for u in photo_urls[:1]
                ):
                    img_html = "\n".join(
                        f'<img src="{url}" style="width:100%; margin-bottom:10px;" alt="상품 이미지">'
                        for url in photo_urls
                    )
                    cleaned_desc = cleaned_desc + "\n" + img_html

                payload["description"] = cleaned_desc

            if not payload:
                continue

            # WC 업데이트
            try:
                resp_put = wc_api.put(f"products/{wc_id}", payload)
                if resp_put.status_code == 200:
                    logger.info(f"    ✅ 수정 완료")
                else:
                    stats["errors"] += 1
                    logger.error(f"    ❌ 실패: {resp_put.status_code}")
            except Exception as e:
                stats["errors"] += 1
                logger.error(f"    ❌ 에러: {e}")

            time.sleep(1.5)

            if (idx + 1) % 20 == 0:
                logger.info(
                    f"  --- 진행: {idx + 1}/{len(targets)} "
                    f"thumb={stats['thumb_fixed']} desc={stats['desc_fixed']} "
                    f"err={stats['errors']}"
                )

    except Exception as e:
        logger.critical(f"치명적 에러: {e}", exc_info=True)
    finally:
        scraper.close()

    # ── 리포트 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ 통합 검증 + 보정 완료")
    logger.info(f"  전체 상품: {total_products}개")
    logger.info(f"  썸네일 수정: {stats['thumb_fixed']}개")
    logger.info(f"  description 수정: {stats['desc_fixed']}개")
    logger.info(f"  밴드 이미지 없음: {stats['band_no_image']}개")
    logger.info(f"  에러: {stats['errors']}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
