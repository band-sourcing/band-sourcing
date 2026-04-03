import logging
from woocommerce import API as WooAPI

from src.content_parser import ParsedProduct, format_product_name
from src.margin_engine import calculate_margin
from src.database import Database

logger = logging.getLogger(__name__)


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
        self.notice_image_url = config["images"]["notice_url"]

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
        images = [{"src": self.notice_image_url, "position": 0}]
        for i, url in enumerate(photo_urls):
            images.append({"src": url, "position": i + 1})

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

            return "created"
        else:
            logger.error(f"WC 상품 생성 실패: {resp.status_code} {resp.text}")
            return "error"


if __name__ == "__main__":
    print("wc_uploader.py 로드 성공!")
    print("실제 테스트는 WC_CONSUMER_KEY/SECRET 설정 후 가능합니다.")
