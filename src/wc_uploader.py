import time
import logging
import httpx
from woocommerce import API as WooAPI

from src.content_parser import ParsedProduct, format_product_name
from src.margin_engine import calculate_margin
from src.database import Database

logger = logging.getLogger(__name__)

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

        raw_notice_url = config["images"].get("notice_url", "")
        self.notice_image_url = self._validate_image_url(raw_notice_url)

        self.no_image_mode = config.get("no_image_mode", "register")

        self._wc_cat_config = config.get("wc_categories", {})

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

    # ── WC 카테고리 매핑 ──

    def _resolve_wc_category(self, category: str, product_name: str, gender: str = "male") -> int:
        """
        카테고리 + 성별 → WC 카테고리 ID.

        - bag/watch/accessory/wallet/shoes/etc → 성별 무관 (독립 카테고리)
        - outer/top/bottom → 성별에 따라 남성/여성 하위 카테고리
        """
        # 성별 무관 카테고리
        _GENDER_FREE = ("bag", "watch", "accessory", "wallet", "shoes")
        if category in _GENDER_FREE:
            return self._wc_cat_config.get(category, self._wc_cat_config.get("etc", 89))

        # 성별 기반 카테고리 (outer / top / bottom)
        if category in ("outer", "top", "bottom"):
            gender_conf = self._wc_cat_config.get(gender, {})
            if isinstance(gender_conf, dict):
                cat_id = gender_conf.get(category)
                if cat_id:
                    return cat_id

        # fallback → etc
        return self._wc_cat_config.get("etc", 89)

    # ── Description 빌드 ──

    def _build_description(self, product: ParsedProduct, photo_urls: list[str]) -> str:
        """
        상세페이지 description HTML 생성.
        순서: ① 공지이미지 ② 본문텍스트 ③ 상품이미지 세로나열
        """
        parts = []

        if self.notice_image_url:
            parts.append(
                f'<img src="{self.notice_image_url}" '
                f'style="width:100%; margin-bottom:20px;" '
                f'alt="공지사항">'
            )

        if product.raw_content:
            text_html = product.raw_content.replace('\n', '<br>')
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

        return '\n'.join(parts)

    def _build_product_data(self, product: ParsedProduct, sell_price: int, photo_urls: list[str], category: str = 'etc', gender: str = 'male') -> dict:
        images = []
        if photo_urls:
            images.append({"src": photo_urls[0], "position": 0})

        name = format_product_name(product.brand_name_en, product.product_name)

        wc_cat_id = self._resolve_wc_category(category, product.product_name, gender)

        return {
            "name": name,
            "type": "simple",
            "status": "publish",
            "regular_price": str(sell_price),
            "description": self._build_description(product, photo_urls),
            "short_description": name,
            "categories": [{"id": wc_cat_id}],
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
        post_key: str,
        gender: str = "male",
    ) -> str:
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

        wc_data = self._build_product_data(product, sell_price, photo_urls, category, gender)

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
