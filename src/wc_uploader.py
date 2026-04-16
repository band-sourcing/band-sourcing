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
        self._duplicate_config = config.get("duplicate", {})

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
        """카테고리 → WC 카테고리 ID (성별 구분 제거)."""
        return self._wc_cat_config.get(category, self._wc_cat_config.get("etc", 89))

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

    def _is_duplicate_enabled(self, category: str) -> bool:
        """카테고리의 중복 허용 여부 확인. 설정 없으면 기본 false(중복 제거)."""
        cat_conf = self._duplicate_config.get(category, {})
        return cat_conf.get("enabled", False)

    def _get_max_count(self, category: str) -> int | None:
        """카테고리의 최대 등록 수. None이면 무제한."""
        cat_conf = self._duplicate_config.get(category, {})
        return cat_conf.get("max_count")

    def _select_bag_variants(self, brand_tag: str, product_name: str,
                             set_part: str, new_cost_price: int,
                             max_count: int) -> list[int]:
        """
        가방 max_count 로직: 최저가 + 중간가만 유지.
        현재 DB 변형 + 신규 가격을 합쳐서 상위 max_count개 선별 후
        탈락한 기존 상품의 product_id 리스트 반환 (WC 삭제 + DB 삭제 대상).
        """
        existing = self.db.list_product_variants(brand_tag, product_name, set_part)
        all_prices = [row["cost_price"] for row in existing]

        if new_cost_price not in all_prices:
            all_prices.append(new_cost_price)

        all_prices_sorted = sorted(set(all_prices))

        if len(all_prices_sorted) <= max_count:
            return []

        # 최저가 + 중간가 선별
        keep_prices = set()
        keep_prices.add(all_prices_sorted[0])  # 최저가
        if max_count >= 2 and len(all_prices_sorted) >= 2:
            mid_idx = len(all_prices_sorted) // 2
            keep_prices.add(all_prices_sorted[mid_idx])  # 중간가

        # 탈락 대상 (기존 등록 상품 중 keep_prices에 없는 것)
        to_delete = []
        for row in existing:
            if row["cost_price"] not in keep_prices:
                to_delete.append(row)

        return to_delete

    def _delete_wc_product(self, wc_product_id: int, db_product_id: int) -> bool:
        """WC 상품 삭제 + DB 레코드 삭제."""
        try:
            resp = self.api.delete(f"products/{wc_product_id}", params={"force": True})
            if resp.status_code == 200:
                self.db.delete_product(db_product_id)
                logger.info(f"  삭제(초과): wc_id={wc_product_id} db_id={db_product_id}")
                time.sleep(0.5)
                return True
            else:
                logger.error(f"WC 삭제 실패: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"WC 삭제 예외: {e}")
            return False

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

        dup_enabled = self._is_duplicate_enabled(category)

        # ── 중복 제거 모드 (bottom/accessory/wallet/shoes) ──
        if not dup_enabled:
            existing = self.db.find_product(
                product.brand_tag, product.product_name, product.set_part
            )
            if existing:
                if existing["cost_price"] == product.cost_price:
                    return "skipped"
                # 가격 다르면 median 업데이트 (기존 로직 유지)
                self.db.add_price_history(
                    product.brand_tag, product.product_name,
                    product.set_part, product.cost_price, post_key
                )
                prices = self.db.get_price_history(
                    product.brand_tag, product.product_name, product.set_part
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

        # ── 중복 허용 모드 (bag/watch/outer/top) ──
        else:
            # 동일 가격 상품이 이미 있으면 스킵
            exact = self.db.find_product_exact(
                product.brand_tag, product.product_name,
                product.set_part, product.cost_price
            )
            if exact:
                return "skipped"

            max_count = self._get_max_count(category)

            # max_count 제한이 있는 경우 (가방: 2개)
            if max_count is not None:
                to_delete = self._select_bag_variants(
                    product.brand_tag, product.product_name,
                    product.set_part, product.cost_price, max_count
                )
                # 탈락 상품 WC + DB 삭제
                for row in to_delete:
                    self._delete_wc_product(row["wc_product_id"], row["id"])

                # 삭제 후 다시 카운트해서 max_count 이상이면 등록 불가
                current_count = self.db.count_product_variants(
                    product.brand_tag, product.product_name, product.set_part
                )
                if current_count >= max_count:
                    # 신규 가격이 keep_prices에 포함되지 않으면 스킵
                    return "skipped"

            # price_history에 기록 (중복 허용이어도 히스토리는 유지)
            self.db.add_price_history(
                product.brand_tag, product.product_name,
                product.set_part, product.cost_price, post_key
            )

        # ── 신규 WC 등록 (공통) ──
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

            # 중복 제거 모드 신규등록 시에만 price_history 추가
            if not dup_enabled:
                self.db.add_price_history(
                    product.brand_tag, product.product_name,
                    product.set_part, product.cost_price, post_key
                )

            time.sleep(1)
            return "created"
        else:
            logger.error(f"WC 상품 생성 실패: {resp.status_code} {resp.text}")
            return "error"


if __name__ == "__main__":
    print("wc_uploader.py 로드 성공!")
    print("실제 테스트는 WC_CONSUMER_KEY/SECRET 설정 후 가능합니다.")
