import time
import logging
import httpx
from woocommerce import API as WooAPI

from src.content_parser import ParsedProduct, format_product_name
from src.margin_engine import calculate_margin
from src.database import Database

logger = logging.getLogger(__name__)

# 이미지 없는 상품용 WooCommerce 기본 placeholder
# WC가 기본 제공하는 placeholder 이미지 (플러그인 불필요)
WC_PLACEHOLDER = "https://choonsik1.com/wp-content/plugins/woocommerce/assets/images/placeholder.png"


class WooCommerceUploader:
    def __init__(self, config: dict, db: Database):
        self.api = WooAPI(
            url=config["woocommerce"]["url"],
            consumer_key=config["woocommerce"]["consumer_key"],
            consumer_secret=config["woocommerce"]["consumer_secret"],
            version="wc/v3",
            timeout=30
        )
        self.db = db
        self.margin_config = config["margin"]

        # 공지 이미지 URL (404이면 None으로 처리)
        raw_notice_url = config["images"].get("notice_url", "")
        self.notice_image_url = self._validate_image_url(raw_notice_url)

        # 이미지 없는 상품 처리 모드: "skip" | "placeholder" | "register"
        self.no_image_mode = config.get("no_image_mode", "register")

        # WC 카테고리 ID 캐시 (margin category -> WC category ID)
        self._wc_category_cache: dict[str, int] = {}
        self._wc_categories_config = config.get("wc_categories", {})
        self._init_wc_categories()

    @staticmethod
    def _validate_image_url(url: str) -> str | None:
        """이미지 URL이 실제로 접근 가능한지 HEAD 요청으로 확인."""
        if not url:
            return None
        try:
            resp = httpx.head(url, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                return url
            else:
                logger.warning(f"공지 이미지 URL 접근 불가 ({resp.status_code}): {url}")
                return None
        except Exception as e:
            logger.warning(f"공지 이미지 URL 확인 실패: {e}")
            return None

    # ── WC 카테고리 초기화 ──

    def _init_wc_categories(self):
        """WC 카테고리 조회/생성하여 ID 캐싱."""
        if not self._wc_categories_config:
            logger.info("wc_categories 설정 없음 → 카테고리 매핑 건너뜀")
            return

        try:
            # 기존 WC 카테고리 전체 조회
            existing = {}
            page = 1
            while True:
                resp = self.api.get("products/categories", params={
                    "per_page": 100,
                    "page": page
                })
                if resp.status_code != 200:
                    logger.error(f"WC 카테고리 조회 실패: {resp.status_code}")
                    break
                cats = resp.json()
                if not cats:
                    break
                for cat in cats:
                    existing[cat["name"]] = cat["id"]
                page += 1

            logger.info(f"기존 WC 카테고리: {list(existing.keys())}")

            # 설정된 카테고리 매핑
            for margin_cat, cat_config in self._wc_categories_config.items():
                cat_name = cat_config["name"]

                if cat_name in existing:
                    self._wc_category_cache[margin_cat] = existing[cat_name]
                    logger.info(f"카테고리 매핑: {margin_cat} -> {cat_name} (ID: {existing[cat_name]})")
                else:
                    # 새로 생성
                    create_resp = self.api.post("products/categories", {
                        "name": cat_name
                    })
                    if create_resp.status_code == 201:
                        new_id = create_resp.json()["id"]
                        self._wc_category_cache[margin_cat] = new_id
                        logger.info(f"카테고리 생성: {margin_cat} -> {cat_name} (ID: {new_id})")
                    else:
                        logger.error(f"카테고리 생성 실패 ({cat_name}): {create_resp.status_code} {create_resp.text}")

        except Exception as e:
            logger.error(f"WC 카테고리 초기화 실패: {e}")

    def _get_wc_category_id(self, margin_category: str) -> int | None:
        """마진 카테고리 코드 → WC 카테고리 ID."""
        return self._wc_category_cache.get(margin_category)

    # ── Description 빌드 (FIX-1 + FIX-3 + FIX-6) ──

    def _build_description(self, product: ParsedProduct, photo_urls: list[str]) -> str:
        """
        상세페이지 description HTML 생성.
        순서:
          ① 공지사항 이미지
          ② 본문 원문 텍스트 (민감정보 제거됨)
          ③ 상품 이미지 전체 세로 나열
        """
        parts = []

        # ① 공지사항 이미지 (맨 위)
        if self.notice_image_url:
            parts.append(
                f'<img src="{self.notice_image_url}" '
                f'style="width:100%; margin-bottom:20px;" '
                f'alt="공지사항">'
            )

        # ② 본문 텍스트 (raw_content - 민감정보 이미 제거됨)
        if product.raw_content:
            # 줄바꿈을 <br>로 변환
            text_html = product.raw_content.replace('\n', '<br>')
            parts.append(
                f'<div style="margin-bottom:20px; line-height:1.8; '
                f'font-size:14px;">{text_html}</div>'
            )

        # ③ 상품 이미지 세로 나열 (모든 이미지)
        for url in photo_urls:
            parts.append(
                f'<img src="{url}" '
                f'style="width:100%; margin-bottom:10px;" '
                f'alt="상품 이미지">'
            )

        return '\n'.join(parts)

    def _build_product_data(
        self,
        product: ParsedProduct,
        sell_price: int,
        photo_urls: list[str],
        category: str = "etc"
    ) -> dict:
        # 목록 썸네일용: 첫 번째 사진 1장만 images 배열에 넣음
        images = []
        if photo_urls:
            images.append({"src": photo_urls[0], "position": 0})

        name = format_product_name(product.brand_name_en, product.product_name)

        data = {
            "name": name,
            "type": "simple",
            "status": "publish",
            "regular_price": str(sell_price),
            "description": self._build_description(product, photo_urls),
            "short_description": name,
            "images": images,
            "manage_stock": False,
            "meta_data": [
                {"key": "_brand_tag", "value": product.brand_tag},
                {"key": "_source_band", "value": product.source_band},
                {"key": "_cost_price", "value": str(product.cost_price)},
                {"key": "_season_code", "value": product.season_code},
                {"key": "_set_part", "value": product.set_part or ""},
            ]
        }

        # WC 카테고리 매핑 (FIX-4)
        wc_cat_id = self._get_wc_category_id(category)
        if wc_cat_id:
            data["categories"] = [{"id": wc_cat_id}]

        return data

    @staticmethod
    def _calculate_median(prices: list[int]) -> int:
        sorted_prices = sorted(prices)
        n = len(sorted_prices)
        if n % 2 == 1:
            return sorted_prices[n // 2]
        else:
            return (sorted_prices[n // 2 - 1] + sorted_prices[n // 2]) // 2

    def process_product(
        self,
        product: ParsedProduct,
        sell_price: int,
        margin_applied: int,
        category: str,
        photo_urls: list[str],
        band_key: str,
        post_key: str
    ) -> str:
        # 이미지 없는 상품 처리
        if not photo_urls and self.no_image_mode == "skip":
            logger.info(f"  스킵(이미지없음): {product.brand_tag} {product.product_name}")
            return "skipped"

        existing = self.db.find_product(
            product.brand_tag, product.product_name, product.set_part
        )

        if existing:
            if existing["cost_price"] == product.cost_price:
                return "skipped"
            else:
                self.db.add_price_history(
                    product.brand_tag,
                    product.product_name,
                    product.set_part,
                    product.cost_price,
                    post_key
                )

                prices = self.db.get_price_history(
                    product.brand_tag,
                    product.product_name,
                    product.set_part
                )
                median_cost = self._calculate_median(prices)

                new_margin = calculate_margin(median_cost, category, self.margin_config)
                new_sell_price = median_cost + new_margin

                resp = self.api.put(
                    f"products/{existing['wc_product_id']}",
                    {"regular_price": str(new_sell_price)}
                )

                if resp.status_code == 200:
                    self.db.update_product_price(
                        existing["id"], median_cost, new_sell_price, new_margin
                    )
                    time.sleep(1)
                    return "price_updated"
                else:
                    logger.error(f"WC 가격 업데이트 실패: {resp.status_code} {resp.text}")
                    return "error"

        wc_data = self._build_product_data(product, sell_price, photo_urls, category)

        resp = self.api.post("products", wc_data)

        if resp.status_code == 201:
            wc_product_id = resp.json()["id"]

            self.db.insert_product(
                brand_tag=product.brand_tag,
                product_name=product.product_name,
                set_part=product.set_part,
                cost_price=product.cost_price,
                sell_price=sell_price,
                margin_applied=margin_applied,
                wc_product_id=wc_product_id,
                band_key=band_key,
                post_key=post_key,
                category=category
            )

            self.db.add_price_history(
                product.brand_tag,
                product.product_name,
                product.set_part,
                product.cost_price,
                post_key
            )

            time.sleep(1)
            return "created"
        else:
            logger.error(f"WC 상품 생성 실패: {resp.status_code} {resp.text}")
            return "error"


if __name__ == "__main__":
    print("wc_uploader.py 로드 성공!")
    print("실제 테스트는 WC_CONSUMER_KEY/SECRET 설정 후 가능합니다.")
