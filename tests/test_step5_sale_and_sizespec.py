#!/usr/bin/env python3
"""
[Step 5] SALE 마커 스킵 + 의류천국22 실측치 기반 분류 테스트.

- content_parser._find_product_name_index / parse_single_product: SALE 다음 줄을 상품명으로
- margin_engine._classify_by_size_spec / classify_category: raw_content 기반 bottom/top 폴백
"""

from __future__ import annotations

import pytest

from src.content_parser import (
    _find_product_name_index,
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
# SECTION 1: _find_product_name_index (helper)
# ══════════════════════════════════════════════════════════════════
class TestFindProductNameIndex:
    def test_no_sale_marker_returns_brand_plus_one(self):
        lines = ["#PD", "나일론 재킷", "사이즈 - M L", "088 (QT)"]
        assert _find_product_name_index(lines, 0) == 1

    def test_sale_marker_skipped(self):
        lines = ["#BB", "SALE", "러브기마 반팔", "블랙 M L XL 2XL", "028 (QT)"]
        assert _find_product_name_index(lines, 0) == 2

    def test_sale_lowercase_skipped(self):
        lines = ["#BB", "sale", "반팔", "028 (QT)"]
        assert _find_product_name_index(lines, 0) == 2

    def test_korean_세일_skipped(self):
        lines = ["#BB", "세일", "반팔", "028 (QT)"]
        assert _find_product_name_index(lines, 0) == 2

    def test_sale_at_end_returns_brand_plus_one(self):
        # 브랜드 + SALE 만 있는 경우 -> 뒤에 상품명 없으므로 SALE idx 반환
        lines = ["#GC", "SALE"]
        # idx+1이 범위 밖이면 SALE 자체 인덱스 반환
        assert _find_product_name_index(lines, 0) == 1

    def test_inline_sale_not_skipped(self):
        # "SALE 이벤트 가디건" 같이 SALE이 상품명 일부면 스킵 안 함
        lines = ["#MC", "SALE 이벤트 가디건", "088 (QT)"]
        assert _find_product_name_index(lines, 0) == 1

    def test_brand_not_at_start(self):
        # 브랜드가 첫 줄이 아닐 수도 있음
        lines = ["쓸모없는 prefix", "#FG", "SALE", "오로라 피그먼트티", "028 (QT)"]
        assert _find_product_name_index(lines, 1) == 3


# ══════════════════════════════════════════════════════════════════
# SECTION 2: parse_single_product - SALE 실제 통합 테스트
# ══════════════════════════════════════════════════════════════════
class TestParseSingleProductWithSale:
    def test_sale_bb_shirt_real_case(self):
        # 실데이터 기반 케이스
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
        assert products[0].product_name == "나일론 재킷"

    def test_sale_bag_brand(self):
        # 잡화 밴드 케이스
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
        # 어깨만 있고 가슴 없음 -> None (오탐 방지)
        raw = "#FG\n가방\n어깨 45\n050 (QT)"
        assert _classify_by_size_spec(raw) is None

    def test_chest_only_returns_none(self):
        raw = "#FG\n가방\n가슴 30\n050 (QT)"
        assert _classify_by_size_spec(raw) is None

    def test_bottom_priority_over_top(self):
        # 하의 마커가 우선 (상의 마커도 같이 있어도 하의로)
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
        # 상품명에 "반팔"(top 키워드) 있으면 raw의 허리 키워드 무시하고 top 반환
        raw = "#BB\n러브기마 반팔\n허리 30 32"
        cat = classify_category(
            "러브기마 반팔", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#BB", raw_content=raw,
        )
        assert cat == "top"

    def test_size_spec_fallback_bottom(self):
        # 상품명 "CR 피그먼트"(키워드 미매칭) + raw에 허리 -> bottom
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
        # 상품명 "STU 주사위"(키워드 미매칭) + raw에 어깨+가슴 -> top
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
        # 잡화천국22는 size-spec 폴백 적용 안 됨
        raw = "#HM\n네오 가든\n어깨 40 가슴 50"
        cat = classify_category(
            "네오 가든", "잡화천국22", CATEGORY_KEYWORDS,
            brand_tag="#HM", raw_content=raw,
        )
        assert cat == "etc"  # 폴백 안 되고 etc 유지

    def test_empty_raw_content_no_fallback(self):
        cat = classify_category(
            "알수없는상품", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#GC", raw_content="",
        )
        assert cat == "etc"

    def test_default_raw_content_backward_compat(self):
        # raw_content 미제공 시 (기존 호출부 영향 없음)
        cat = classify_category(
            "반팔 티셔츠", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#BB",
        )
        assert cat == "top"

    def test_watch_brand_fallback_still_works(self):
        # 시계 브랜드 폴백이 size-spec보다 우선
        raw = "#RL\n데이저스트\n크라운 40"
        cat = classify_category(
            "알수없는시계", "잡화천국22", CATEGORY_KEYWORDS,
            brand_tag="#RL", raw_content=raw,
        )
        assert cat == "watch"


# ══════════════════════════════════════════════════════════════════
# SECTION 5: 실데이터 패턴 회귀 테스트 (audit 결과 기반)
# ══════════════════════════════════════════════════════════════════
class TestRealWorldPatterns:
    """audit_for_review.xlsx 실데이터 기반 오분류 복구 검증."""

    def test_sale_bb_with_keyword(self):
        # "SALE" + "반팔" -> 반팔 추출 + top 분류
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
        # "하프"(bottom 키워드) 매칭 -> size-spec 폴백 없이 bottom
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
        # "CR 피그먼트" -> 키워드 "피그먼트" 매칭되어 top (또는 size-spec 폴백으로도 top)
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
        # "[N] 하프문 크로스백" -> 하프(bottom) vs 크로스백(bag) 중 last-match로 bag
        name = "[N] 하프문 크로스백"
        cat = classify_category(
            name, "잡화천국22", CATEGORY_KEYWORDS,
            brand_tag="#NK", raw_content="",
        )
        assert cat == "bag"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
