#!/usr/bin/env python3
import sys
import os
import logging
from datetime import datetime

from src.exclusion_filter import should_exclude
sys.path.insert(0, os.path.dirname(__file__))

from src.config import load_config
from src.database import Database
from src.band_scraper import BandScraper
from src.content_parser import parse_post, ParseError
from src.margin_engine import calculate_sell_price, classify_category, classify_gender
from src.wc_uploader import WooCommerceUploader
from src.auto_delete import auto_delete_old_products
from src.search_supplement import fetch_search_posts, build_search_keywords


def setup_logging(config):
    log_dir = config["logging"]["log_dir"]
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(
        log_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )

    logging.basicConfig(
        level=getattr(logging, config["logging"]["level"]),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )


def main():
    config = load_config()
    setup_logging(config)
    logger = logging.getLogger("main")

    os.makedirs(os.path.dirname(config["database"]["path"]), exist_ok=True)
    db = Database(config["database"]["path"])
    run_id = db.start_run()

    stats = {
        "posts_fetched": 0,
        "products_created": 0,
        "products_skipped": 0,
        "products_updated": 0,
        "products_deleted": 0,
        "parse_errors": 0,
        "no_image_count": 0,
        "errors": []
    }

    try:
        # Step 1: Band 게시글 수집 (Playwright)
        logger.info("=== Step 1: Band 게시글 수집 (Playwright) ===")
        scraper = BandScraper(
            naver_id=config["band"]["naver_id"],
            naver_pw=config["band"]["naver_pw"],
            cutoff_date=config["band"]["cutoff_date"],
            session_path="data/band_session.json",
        )

        # 밴드 키가 설정에 있으면 직접 사용 (검색 우회)
        if config["band"].get("band_keys"):
            band_keys = config["band"]["band_keys"]
        else:
            band_keys = scraper.get_band_keys(config["band"]["target_bands"])
        logger.info(f"밴드 발견: {list(band_keys.keys())}")

        all_posts = []
        for band_name, band_key in band_keys.items():
            posts = scraper.fetch_all_posts(band_key)
            for p in posts:
                p["_source_band"] = band_name
                p["_band_key"] = band_key
            all_posts.extend(posts)
            logger.info(f"  {band_name}: {len(posts)}개 수집")

        # Step 1.5: 검색 기반 보충 수집 (피드 스크롤 한계 우회)
        search_targets = build_search_keywords(config)
        if search_targets:
            logger.info("=== Step 1.5: 검색 기반 보충 수집 ===")
            existing_keys = {p["post_key"] for p in all_posts}
            cutoff_dt = datetime.strptime(config["band"]["cutoff_date"], "%Y-%m-%d")

            for band_name, keywords in search_targets.items():
                band_key = band_keys.get(band_name)
                if not band_key:
                    logger.warning(f"  검색 대상 밴드 키 없음: {band_name}")
                    continue

                logger.info(f"  {band_name}: {len(keywords)}개 키워드 검색 시작")
                search_posts = fetch_search_posts(
                    scraper, band_key, keywords, cutoff_dt, existing_keys
                )

                for p in search_posts:
                    p["_source_band"] = band_name
                    p["_band_key"] = band_key
                    existing_keys.add(p["post_key"])

                all_posts.extend(search_posts)
                logger.info(f"  {band_name}: 검색 보충 {len(search_posts)}개 추가")

        scraper.close()
        stats["posts_fetched"] = len(all_posts)
        logger.info(f"총 {len(all_posts)}개 게시글 수집 완료")

        # Step 2: 처리 완료된 게시글 필터링
        logger.info("=== Step 2: 신규 게시글 필터링 ===")
        new_posts = [
            post for post in all_posts
            if not db.is_post_processed(post["post_key"])
        ]
        logger.info(f"신규 게시글: {len(new_posts)}개")

        # Step 3: 파싱 + 마진 + 등록
        logger.info("=== Step 3: 상품 파싱 및 등록 ===")
        uploader = WooCommerceUploader(config, db)

        for post in new_posts:
            try:
                if not post.get("content"):
                    logger.debug(f"content 없음 (post_key={post['post_key']}), 건너뜀")
                    continue
                products = parse_post(
                    content=post["content"],
                    brand_map=config["brand_map"],
                    source_band=post["_source_band"]
                )

                for product in products:
                    # Exclusion Filter
                    if should_exclude(product, config.get("exclusion", {})):
                        stats["products_skipped"] += 1
                        continue
                    category = classify_category(
                        product.product_name,
                        product.source_band,
                        config["category_keywords"],
                        brand_tag=product.brand_tag,
                        golf_brand_tags=config.get("golf_brand_tags", []),
                        keyword_exclusions=config.get("keyword_exclusions", {}),
                        raw_content=post["content"],
                        set_part=product.set_part,
                    )

                    gender = classify_gender(
                        product.sizes,
                        config.get("gender_classification", {}),
                    )

                    sell_price, margin = calculate_sell_price(
                        product.cost_price,
                        category,
                        config["margin"]
                    )

                    photo_urls = [
                        p["url"] for p in post.get("photos", [])
                        if not p.get("is_video_thumbnail", False)
                    ]

                    if not photo_urls:
                        stats["no_image_count"] += 1

                    result = uploader.process_product(
                        product=product,
                        sell_price=sell_price,
                        margin_applied=margin,
                        category=category,
                        photo_urls=photo_urls,
                        band_key=post["_band_key"],
                        post_key=post["post_key"],
                        gender=gender,
                    )

                    if result == "created":
                        stats["products_created"] += 1
                        img_info = f"(이미지 {len(photo_urls)}장)" if photo_urls else "(이미지 없음)"
                        logger.info(f"  등록: {product.brand_tag} {product.product_name} {img_info}")
                    elif result == "skipped":
                        stats["products_skipped"] += 1
                    elif result == "price_updated":
                        stats["products_updated"] += 1
                        logger.info(f"  가격수정: {product.brand_tag} {product.product_name}")
                    elif result == "error":
                        stats["errors"].append(f"WC: {post['post_key']}")

                db.mark_post_processed(post["_band_key"], post["post_key"])

            except ParseError as e:
                stats["parse_errors"] += 1
                logger.warning(f"파싱 실패 (post_key={post['post_key']}): {e}")
                stats["errors"].append(f"Parse: {post['post_key']}: {str(e)}")
                # 파싱 실패해도 processed로 마킹 (재시도 방지)
                db.mark_post_processed(post["_band_key"], post["post_key"])
            except Exception as e:
                logger.error(f"처리 실패 (post_key={post['post_key']}): {e}", exc_info=True)
                stats["errors"].append(f"Error: {post['post_key']}: {str(e)}")

        # Step 4: Auto-Delete
        logger.info("=== Step 4: 자동 삭제 ===")
        if config["auto_delete"]["enabled"]:
            deleted = auto_delete_old_products(
                db=db,
                wcapi=uploader.api,
                max_products=config["auto_delete"]["max_products"]
            )
            stats["products_deleted"] = deleted
        else:
            logger.info("자동 삭제 OFF")

        # 완료
        db.finish_run(run_id, stats, "success")
        logger.info(
            f"=== 파이프라인 완료 === "
            f"생성={stats['products_created']} "
            f"건너뜀={stats['products_skipped']} "
            f"업데이트={stats['products_updated']} "
            f"삭제={stats['products_deleted']} "
            f"파싱에러={stats['parse_errors']} "
            f"이미지없음={stats['no_image_count']} "
            f"에러={len(stats['errors'])}"
        )

    except Exception as e:
        logger.critical(f"파이프라인 치명적 에러: {e}", exc_info=True)
        stats["errors"].append(f"CRITICAL: {str(e)}")
        db.finish_run(run_id, stats, "failed")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
