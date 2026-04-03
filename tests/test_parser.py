#!/usr/bin/env python3
"""Content Parser 단위 테스트."""

import pytest

from src.content_parser import (
    ParsedProduct,
    ParseError,
    parse_post,
    parse_single_product,
    parse_set_product,
    is_set_product,
    preprocess_content,
    _extract_brand,
    _extract_price,
    _extract_sizes,
)

BRAND_MAP = {
    "#PD": "PRADA",
    "#NK": "NIKE",
    "#AZ": "AMAZINGCORE",
    "#GC": "GUCCI",
    "#LV": "LOUIS VUITTON",
}


# ── 일반 상품 (잡화) 파싱 ──

class TestSingleProductAccessory:
    """잡화천국22 일반 상품 파싱."""

    CONTENT = """#PD
아르케 리나일론 숄더
사이즈 : 22.0 x 18.0 x 6.0 cm
121 (AI24)"""

    def test_brand_tag(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert products[0].brand_tag == "#PD"

    def test_brand_name(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert products[0].brand_name_en == "PRADA"

    def test_product_name(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert products[0].product_name == "아르케 리나일론 숄더"

    def test_cost_price(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert products[0].cost_price == 121000

    def test_season_code(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert products[0].season_code == "AI24"

    def test_source_band(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert products[0].source_band == "잡화천국22"

    def test_not_set_product(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert products[0].set_part is None

    def test_single_product_count(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "잡화천국22")
        assert len(products) == 1


# ── 일반 상품 (의류) 파싱 ──

class TestSingleProductClothing:
    """의류천국22 일반 상품 파싱."""

    CONTENT = """#NK
로* 윈드배색바람막이
색상-블랙,화이트,그레이
사이즈-남여공용 FREE
총장72 가슴65
050 (BM)"""

    def test_brand_tag(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        assert products[0].brand_tag == "#NK"

    def test_colors(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        assert "블랙" in products[0].colors
        assert "화이트" in products[0].colors
        assert "그레이" in products[0].colors

    def test_sizes(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        assert len(products[0].sizes) > 0

    def test_measurements(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        assert products[0].measurements is not None
        assert "총장72" in products[0].measurements

    def test_cost_price(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        assert products[0].cost_price == 50000


# ── 세트 상품 파싱 ──

class TestSetProduct:
    """의류천국22 세트 상품 파싱."""

    CONTENT = """#AZ
"네오테크 후디 셋업"
색상: 그레이/ 블랙
상의: 95(M)/ 100(L)/ 105(XL)/ 110(XXL)
하의: 30(M)/ 32(L)/ 34(XL)/ 36(XXL)
상의 053 (AL)
하의 046 (AL)"""

    def test_returns_two_products(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        assert len(products) == 2

    def test_top_set_part(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        top = [p for p in products if p.set_part == "top"]
        assert len(top) == 1

    def test_bottom_set_part(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        bottom = [p for p in products if p.set_part == "bottom"]
        assert len(bottom) == 1

    def test_top_price(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        top = [p for p in products if p.set_part == "top"][0]
        assert top.cost_price == 53000

    def test_bottom_price(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        bottom = [p for p in products if p.set_part == "bottom"][0]
        assert bottom.cost_price == 46000

    def test_top_product_name(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        top = [p for p in products if p.set_part == "top"][0]
        assert "상의" in top.product_name

    def test_bottom_product_name(self):
        products = parse_post(self.CONTENT, BRAND_MAP, "의류천국22")
        bottom = [p for p in products if p.set_part == "bottom"][0]
        assert "하의" in bottom.product_name


# ── 공장 코드 추출 ──

class TestFactoryCode:
    """시즌코드(=공장코드) 추출 확인."""

    def test_factory_code_extracted(self):
        content = "#GC\n구찌 토트백\n350 (QE)\n"
        products = parse_post(content, BRAND_MAP, "잡화천국22")
        assert products[0].season_code == "QE"

    def test_factory_code_alphanumeric(self):
        content = "#PD\n프라다 숄더백\n121 (AI24)\n"
        products = parse_post(content, BRAND_MAP, "잡화천국22")
        assert products[0].season_code == "AI24"


# ── FREE 사이즈 감지 ──

class TestFreeSizeDetection:
    """FREE 사이즈 감지."""

    def test_free_in_sizes(self):
        content = """#NK
테스트 자켓
색상-블랙
사이즈-FREE
050 (BM)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        sizes = products[0].sizes
        assert any(s.strip().upper() == "FREE" for s in sizes)

    def test_non_free_sizes(self):
        content = """#NK
테스트 자켓
색상-블랙
사이즈-M,L,XL
050 (BM)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        sizes = products[0].sizes
        assert not any(s.strip().upper() == "FREE" for s in sizes)


# ── 에러 케이스 ──

class TestParseErrors:
    """파싱 에러 케이스."""

    def test_no_brand_tag_raises(self):
        content = "브랜드없는 상품\n121 (AI24)\n"
        with pytest.raises(ParseError):
            parse_post(content, BRAND_MAP, "잡화천국22")

    def test_no_price_raises(self):
        content = "#PD\n프라다 숄더백\n가격정보없음\n"
        with pytest.raises(ParseError):
            parse_post(content, BRAND_MAP, "잡화천국22")
