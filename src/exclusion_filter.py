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
    """상품 사이즈에 FREE가 포함되어 있는지 확인.

    밴드 게시글에서 FREE가 다양한 형태로 적힘:
    - "FREE" / "free" / "Free"
    - "남여공용 FREE" (사이즈 파싱 시 통째로 하나의 문자열)
    - "프리" / "프리사이즈" / "F"
    """
    _FREE_MARKERS = {"free", "프리", "프리사이즈"}

    for size in product.sizes:
        s = size.strip()
        s_upper = s.upper()
        # exact match
        if s_upper == "FREE" or s_upper == "F":
            return True
        # contains match (예: "남여공용 FREE")
        if "FREE" in s_upper:
            return True
        # 한국어 변형
        if s in _FREE_MARKERS:
            return True

    # raw_content에서도 FREE 키워드 검색 (사이즈 파싱이 아예 안 된 경우 대비)
    if product.raw_content:
        content_upper = product.raw_content.upper()
        if "사이즈" in product.raw_content and "FREE" in content_upper:
            return True
        if "프리사이즈" in product.raw_content:
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
