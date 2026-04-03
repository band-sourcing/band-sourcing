import re
import logging

from src.content_parser import ParsedProduct

logger = logging.getLogger(__name__)


def _extract_factory_code(season_code: str) -> str | None:
    """시즌코드에서 공장코드(알파벳 부분)를 추출. 예: 'AI24' → None, 'BM' → 'BM', 'AL' → 'AL'"""
    if not season_code:
        return None
    return season_code.strip()


def _is_excluded_factory(season_code: str, excluded_codes: list[str]) -> bool:
    """공장코드가 제외 목록에 해당하는지 확인 (대소문자 무시)"""
    code = _extract_factory_code(season_code)
    if not code:
        return False
    code_lower = code.lower()
    return code_lower in [c.lower() for c in excluded_codes]


def _has_free_size(product: ParsedProduct) -> bool:
    """상품 사이즈에 FREE가 포함되어 있는지 확인"""
    for size in product.sizes:
        if size.strip().upper() == "FREE":
            return True
    return False


def should_exclude(product: ParsedProduct, exclusion_config: dict) -> bool:
    """
    제외 필터. True를 반환하면 해당 상품은 건너뛴다.

    1) factory_codes: season_code가 제외 목록에 해당하면 제외
    2) free_size: target_bands에 속한 밴드의 FREE 사이즈 상품 제외
    """
    # 공장 코드 제외
    fc_config = exclusion_config.get("factory_codes", {})
    if fc_config.get("enabled", False):
        codes = fc_config.get("codes", [])
        if _is_excluded_factory(product.season_code, codes):
            logger.info(
                f"  제외(공장코드): {product.brand_tag} {product.product_name} "
                f"(code={product.season_code})"
            )
            return True

    # FREE 사이즈 제외
    fs_config = exclusion_config.get("free_size", {})
    if fs_config.get("enabled", False):
        target_bands = fs_config.get("target_bands", [])
        if product.source_band in target_bands and _has_free_size(product):
            logger.info(
                f"  제외(FREE사이즈): {product.brand_tag} {product.product_name} "
                f"(band={product.source_band})"
            )
            return True

    return False
