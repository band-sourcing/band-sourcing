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

    def _build_description(self, product: ParsedProduct) -> str:
        parts = []
        if product.colors:
            parts.append(f"색상: {', '.join(product.colors)}")
        if product.sizes:
            parts.append(f"사이즈: {', '.join(product.sizes)}")
        if product.measurements:
            parts.append(f"실측: {product.measurements}")
        return "<br>".join(parts)

    def _build_product_data(self, product: ParsedProduct, sell_price: int, photo_urls: list[str]) -> dict:
        images = []
        pos = 0

        # 상품 이미지 (첫 번째를 대표 이미지로)
        if photo_urls:
            images.append({"src": photo_urls[0], "position": pos})
            pos += 1

        # 공지 이미지 (존재할 때만)
        if self.notice_image_url:
            images.append({"src": self.notice_image_url, "position": pos})
            pos += 1

        # 나머지 상품 이미지
        for url in photo_urls[1:]:
            images.append({"src": url, "position": pos})
            pos += 1

        # 이미지가 하나도 없으면 빈 배열 (WC가 기본 placeholder 사용)
        # images가 []이면 WC는 에러 없이 placeholder로 표시

        name = format_product_name(product.brand_name_en, product.product_name)

        return {
            "name": name,
            "type": "simple",
            "status": "publish",
            "regular_price": str(sell_price),
            "description": self._build_description(product),
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

        wc_data = self._build_product_data(product, sell_price, photo_urls)

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
