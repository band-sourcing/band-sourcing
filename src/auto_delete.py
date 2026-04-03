import logging

from src.database import Database

logger = logging.getLogger(__name__)


def auto_delete_old_products(db: Database, wcapi, max_products: int = 1500) -> int:
    total = db.count_products()

    if total <= max_products:
        logger.info(f"상품 수 {total}개 (한도 {max_products}) - 삭제 불필요")
        return 0

    to_delete = total - max_products
    logger.info(f"상품 수 {total}개 > 한도 {max_products} - {to_delete}개 삭제 예정")

    old_products = db.get_oldest_products(limit=to_delete)

    deleted = 0
    for product in old_products:
        try:
            resp = wcapi.delete(
                f"products/{product['wc_product_id']}",
                params={"force": True}
            )

            if resp.status_code == 200:
                db.delete_product(product["id"])
                deleted += 1
                logger.info(f"삭제 완료: id={product['id']} wc_id={product['wc_product_id']}")
            else:
                logger.warning(
                    f"WC 삭제 실패: wc_id={product['wc_product_id']} "
                    f"status={resp.status_code}"
                )
        except Exception as e:
            logger.error(f"삭제 에러: wc_id={product['wc_product_id']} {e}")

    logger.info(f"자동 삭제 완료: {deleted}/{to_delete}개 삭제")
    return deleted


if __name__ == "__main__":
    print("auto_delete.py 로드 성공!")
    print("실제 테스트는 WooCommerce 연동 후 가능합니다.")
