#!/usr/bin/env python3
"""
[Step 5 + Task 8] SALE 마커 스킵 + 실측치 분류 + 토큰 기반 추출 테스트.

- content_parser.extract_product_name_from_tokens: 토큰 기반 상품명 추출 (Task 8)
- content_parser.parse_single_product: 토큰 기반 통합
- margin_engine.classify_category: raw_content 기반 bottom/top 폴백
"""

from __future__ import annotations

import pytest

from src.content_parser import (
    extract_product_name_from_tokens,
    parse_post,
    parse_single_product,
)
from src.margin_engine import (
    _classify_by_size_spec,
    classify_category,
)


BRAND_MAP = {
    "#BB": "버버리",
    "#PD": "프라다",
    "#MC": "몽클",
    "#NK": "나이키",
    "#GC": "구찌",
    "#LV": "루이비통",
    "#SS": "스투시",
    "#CH": "크롬하츠",
    "#FG": "페레가모",
}

# 실제 밴드 키워드 카테고리(audit 보강 후 기준) 축약판
CATEGORY_KEYWORDS = {
    "bag": ["가방", "백", "토트", "숄더백", "크로스백", "하프문", "스피디"],
    "watch": ["시계", "워치"],
    "wallet": ["지갑", "카드홀더", "카드지갑", "카드", "아코디언", "3단"],
    "shoes": ["스니커즈", "로퍼", "에스파듀"],
    "outer": ["자켓", "재킷", "패딩", "코트", "가디건"],
    "top": ["반팔", "티셔츠", "워싱티", "피그먼트티", "하프문셔츠"],
    "bottom": ["팬츠", "데님", "슬랙스", "하프", "쇼트", "밴딩"],
    "accessory": ["벨트", "모자", "햇", "삭스", "스타킹", "썬그라스", "선그라스", "팬티", "헤어핀"],
}


# ══════════════════════════════════════════════════════════════════
# SECTION 1: extract_product_name_from_tokens (Task 8)
# ══════════════════════════════════════════════════════════════════
class TestExtractProductNameFromTokens:
    """토큰 기반 상품명 추출기 단위 테스트."""

    def test_basic_product(self):
        raw = "#PD\n나일론 재킷\n088 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert "나일론 재킷" in result
        assert "#PD" not in result
        assert "088" not in result
        assert "QT" not in result

    def test_br_tag_converted_to_space(self):
        raw = "#FG<br><br>SALE<br><br>반팔<br><br>028 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert "반팔" in result
        assert "<br>" not in result
        assert "SALE" not in result
        assert "#FG" not in result

    def test_sale_marker_removed(self):
        raw = """#BB

SALE

러브기마 반팔

028 (QT)"""
        result = extract_product_name_from_tokens(raw)
        assert result == "러브기마 반팔"

    def test_size_spec_block_removed(self):
        raw = """#BB

러브기마 반팔

SIZE SPEC
어깨 44 45 46 48
가슴 50 52 54 57

028 (QT)"""
        result = extract_product_name_from_tokens(raw)
        assert result == "러브기마 반팔"
        assert "어깨" not in result
        assert "44" not in result

    def test_size_measurements_block_removed(self):
        # SIZE SPEC 헤더 없이 실측치만 나오는 경우
        raw = "#NK 후디 어깨 44 45 가슴 50 52 028 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert "후디" in result
        assert "어깨" not in result

    def test_color_size_line_removed(self):
        raw = """#BB

반팔

블랙 M L XL 2XL
화이트 M L XL 2XL

028 (QT)"""
        result = extract_product_name_from_tokens(raw)
        assert result == "반팔"

    def test_fabric_description_removed(self):
        raw = "#BB 반팔 나일론 스판텍스 기능성 소재 028 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert result == "반팔"

    def test_emoji_removed(self):
        raw = "#GC\n아코디오 플리츠 스커츠 🍀\n055 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert "🍀" not in result
        assert "아코디오" in result

    def test_conservative_preserves_normal_names(self):
        """공격적 제거로 정상 상품명이 파괴되지 않는지 확인."""
        raw = "#NK\nK26 멀티 컬러 크로스 후드집업\n088 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert "K26" in result
        assert "멀티" in result
        assert "후드집업" in result

    def test_cutoff_on_product_info_section(self):
        """상품 구성 설명 이후는 잘림."""
        raw = "#NK\nB33 스니커즈\n-color : 네이비\n-사이즈 36\n088 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert "B33 스니커즈" in result
        assert "네이비" not in result

    def test_empty_input(self):
        assert extract_product_name_from_tokens("") == ""
        assert extract_product_name_from_tokens(None) == ""

    def test_only_meta_returns_empty(self):
        raw = "#BB 028 (QT) SALE"
        result = extract_product_name_from_tokens(raw)
        assert result == "" or result == " "

    def test_price_code_removed(self):
        raw = "#PD 나일론 재킷 088 (QT)"
        result = extract_product_name_from_tokens(raw)
        assert "088" not in result
        assert "(QT)" not in result


# ══════════════════════════════════════════════════════════════════
# SECTION 2: parse_single_product - 통합 (SALE 포함)
# ══════════════════════════════════════════════════════════════════
class TestParseSingleProductWithSale:
    def test_sale_bb_shirt_real_case(self):
        content = """#BB

SALE

러브기마 반팔

블랙 M L XL 2XL
화이트 M L XL 2XL

나일론 스판텍스 기능성 소재

SIZE SPEC
어깨 44 45 46 48
가슴 50 52 54 57

028 (QT)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        assert len(products) == 1
        assert products[0].product_name == "러브기마 반팔"
        assert products[0].brand_tag == "#BB"
        assert products[0].cost_price == 28000

    def test_no_sale_unchanged(self):
        content = """#PD
나일론 재킷
사이즈 - M L
088 (QT)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        assert "나일론 재킷" in products[0].product_name

    def test_sale_bag_brand(self):
        content = """#LV

SALE

스피디 반둘리에

250 (QT)"""
        products = parse_post(content, BRAND_MAP, "잡화천국22")
        assert products[0].product_name == "스피디 반둘리에"


# ══════════════════════════════════════════════════════════════════
# SECTION 3: _classify_by_size_spec
# ══════════════════════════════════════════════════════════════════
class TestClassifyBySizeSpec:
    def test_empty_raw_returns_none(self):
        assert _classify_by_size_spec("") is None
        assert _classify_by_size_spec(None) is None

    def test_bottom_by_허리(self):
        raw = "#CH\n피그먼트 반바지\n허리 28 30 32\n028 (QT)"
        assert _classify_by_size_spec(raw) == "bottom"

    def test_bottom_by_허벅지(self):
        raw = "#NK\n[N] 조거\n허벅지 23 25\n총장 100\n048 (AL)"
        assert _classify_by_size_spec(raw) == "bottom"

    def test_bottom_by_밑위(self):
        raw = "#GC\n데님\n밑위 26 28\n041 (QI)"
        assert _classify_by_size_spec(raw) == "bottom"

    def test_top_by_어깨_and_가슴(self):
        raw = "#BB\nSALE\n러브기마 반팔\n어깨 44 45 46 48\n가슴 50 52 54 57\n028 (QT)"
        assert _classify_by_size_spec(raw) == "top"

    def test_top_with_sleeve_still_top(self):
        raw = "#SS\nSTU 하프\n어깨 40\n가슴 55\n소매 25\n기장 70\n028 (QT)"
        assert _classify_by_size_spec(raw) == "top"

    def test_shoulder_only_returns_none(self):
        raw = "#FG\n가방\n어깨 45\n050 (QT)"
        assert _classify_by_size_spec(raw) is None

    def test_chest_only_returns_none(self):
        raw = "#FG\n가방\n가슴 30\n050 (QT)"
        assert _classify_by_size_spec(raw) is None

    def test_bottom_priority_over_top(self):
        raw = "상의 65 / 어깨 57 / 가슴 60\n하의 허리 30 / 허벅지 25"
        assert _classify_by_size_spec(raw) == "bottom"

    def test_no_markers_returns_none(self):
        raw = "#GC\n사이즈 FREE\n028 (QT)"
        assert _classify_by_size_spec(raw) is None


# ══════════════════════════════════════════════════════════════════
# SECTION 4: classify_category with raw_content (통합)
# ══════════════════════════════════════════════════════════════════
class TestClassifyCategoryWithRawContent:
    def test_keyword_match_takes_precedence_over_size_spec(self):
        raw = "#BB\n러브기마 반팔\n허리 30 32"
        cat = classify_category(
            "러브기마 반팔", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#BB", raw_content=raw,
        )
        assert cat == "top"

    def test_size_spec_fallback_bottom(self):
        raw = """#CH
CR 피그먼트
블랙 S M L
허리 28 30 32
028 (QT)"""
        cat = classify_category(
            "CR 피그먼트", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#CH", raw_content=raw,
        )
        assert cat == "bottom"

    def test_size_spec_fallback_top(self):
        raw = """#SS
STU 주사위
어깨 44 46
가슴 52 54
028 (QT)"""
        cat = classify_category(
            "STU 주사위", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#SS", raw_content=raw,
        )
        assert cat == "top"

    def test_size_spec_not_applied_to_accessory_band(self):
        raw = "#HM\n네오 가든\n어깨 40 가슴 50"
        cat = classify_category(
            "네오 가든", "잡화천국22", CATEGORY_KEYWORDS,
            brand_tag="#HM", raw_content=raw,
        )
        assert cat == "etc"

    def test_empty_raw_content_no_fallback(self):
        cat = classify_category(
            "알수없는상품", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#GC", raw_content="",
        )
        assert cat == "etc"

    def test_default_raw_content_backward_compat(self):
        cat = classify_category(
            "반팔 티셔츠", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#BB",
        )
        assert cat == "top"

    def test_watch_brand_fallback_still_works(self):
        raw = "#RL\n데이저스트\n크라운 40"
        cat = classify_category(
            "알수없는시계", "잡화천국22", CATEGORY_KEYWORDS,
            brand_tag="#RL", raw_content=raw,
        )
        assert cat == "watch"


# ══════════════════════════════════════════════════════════════════
# SECTION 5: 실데이터 패턴 회귀 테스트
# ══════════════════════════════════════════════════════════════════
class TestRealWorldPatterns:
    def test_sale_bb_with_keyword(self):
        content = """#BB

SALE

러브기마 반팔

블랙 M L XL 2XL

SIZE SPEC
어깨 44 45 46 48
가슴 50 52 54 57

028 (QT)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        p = products[0]
        cat = classify_category(
            p.product_name, p.source_band, CATEGORY_KEYWORDS,
            brand_tag=p.brand_tag, raw_content=content,
        )
        assert p.product_name == "러브기마 반팔"
        assert cat == "top"

    def test_half_pants_by_keyword(self):
        content = """#SS

STU 도쿄 하프

허리 28 30 32

028 (QT)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        p = products[0]
        cat = classify_category(
            p.product_name, p.source_band, CATEGORY_KEYWORDS,
            brand_tag=p.brand_tag, raw_content=content,
        )
        assert cat == "bottom"

    def test_crmark_pigment_by_size_spec(self):
        content = """#CH

CR 피그먼트

블랙 S M L

어깨 44 46 48
가슴 52 54 56
소매 24 25 26

030 (QT)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        p = products[0]
        cat = classify_category(
            p.product_name, p.source_band, CATEGORY_KEYWORDS,
            brand_tag=p.brand_tag, raw_content=content,
        )
        assert cat == "top"

    def test_halfmoon_crossbag_not_misclassified(self):
        name = "[N] 하프문 크로스백"
        cat = classify_category(
            name, "잡화천국22", CATEGORY_KEYWORDS,
            brand_tag="#NK", raw_content="",
        )
        assert cat == "bag"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
