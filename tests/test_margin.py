#!/usr/bin/env python3
"""Margin Engine 단위 테스트 (v2 — 골프삭제 / 지갑·신발 추가 / 마진 50000/40000/30000)."""

import pytest

from src.margin_engine import (
    calculate_margin,
    calculate_sell_price,
    classify_category,
    classify_gender,
)

MARGIN_CONFIG = {
    "price_tiers": [
        {"min_price": 1000000, "margin": 200000},
        {"min_price": 500000, "margin": 150000},
        {"min_price": 300000, "margin": 100000},
    ],
    "category_margins": {
        "bag": 50000,
        "watch": 50000,
        "outer": 40000,
        "top": 30000,
        "bottom": 30000,
        "accessory": 30000,
        "wallet": 30000,
        "shoes": 30000,
        "etc": 30000,
    },
}

CATEGORY_KEYWORDS = {
    "bag": ["가방", "백", "토트", "숄더백", "크로스백", "클러치", "핸드백", "배낭", "백팩"],
    "watch": ["시계", "워치"],
    "wallet": ["지갑", "카드홀더", "카드지갑", "반지갑", "장지갑", "월렛", "카드케이스", "코인지갑", "키홀더", "키링"],
    "shoes": ["스니커즈", "운동화", "로퍼", "구두", "부츠", "슬리퍼", "샌들", "신발", "뮬", "슈즈"],
    "outer": ["자켓", "블루종", "패딩", "코트", "바람막이", "점퍼", "아우터", "윈드", "후드집업", "야상", "무스탕", "트렌치", "베스트"],
    "top": ["티셔츠", "긴팔", "반팔", "맨투맨", "후디", "후드", "니트", "스웨터", "셔츠", "폴로"],
    "bottom": ["팬츠", "바지", "슬랙스", "데님", "청바지"],
    "accessory": ["벨트", "머플러", "선글라스", "모자"],
}

GENDER_CONFIG = {
    "female_keywords": ["우먼", "WOMEN", "여성", "레이디", "걸즈"],
    "male_keywords": ["맨즈", "MEN", "남성"],
    "female_sizes": ["44", "55", "66", "77", "88"],
    "male_sizes": ["90", "95", "100", "105", "110", "115", "120", "30", "32", "34", "36", "38"],
    "default_gender": "male",
}


# ── 가격대별 마진 테스트 ──

class TestPriceTierMargin:
    """가격대별 마진 (30만/50만/100만 이상)."""

    def test_over_1000000(self):
        margin = calculate_margin(1200000, "bag", MARGIN_CONFIG)
        assert margin == 200000

    def test_over_500000(self):
        margin = calculate_margin(550000, "bag", MARGIN_CONFIG)
        assert margin == 150000

    def test_over_300000(self):
        margin = calculate_margin(350000, "outer", MARGIN_CONFIG)
        assert margin == 100000

    def test_under_300000_falls_to_category(self):
        margin = calculate_margin(250000, "bag", MARGIN_CONFIG)
        assert margin == 50000


# ── 카테고리별 마진 테스트 ──

class TestCategoryMargin:
    """카테고리별 마진 (30만 미만일 때)."""

    def test_bag(self):
        margin = calculate_margin(200000, "bag", MARGIN_CONFIG)
        assert margin == 50000

    def test_watch(self):
        margin = calculate_margin(200000, "watch", MARGIN_CONFIG)
        assert margin == 50000

    def test_outer(self):
        margin = calculate_margin(200000, "outer", MARGIN_CONFIG)
        assert margin == 40000

    def test_top(self):
        margin = calculate_margin(200000, "top", MARGIN_CONFIG)
        assert margin == 30000

    def test_bottom(self):
        margin = calculate_margin(200000, "bottom", MARGIN_CONFIG)
        assert margin == 30000

    def test_accessory(self):
        margin = calculate_margin(200000, "accessory", MARGIN_CONFIG)
        assert margin == 30000

    def test_wallet(self):
        margin = calculate_margin(200000, "wallet", MARGIN_CONFIG)
        assert margin == 30000

    def test_shoes(self):
        margin = calculate_margin(200000, "shoes", MARGIN_CONFIG)
        assert margin == 30000

    def test_etc(self):
        margin = calculate_margin(200000, "etc", MARGIN_CONFIG)
        assert margin == 30000


# ── 가격대 우선순위 테스트 ──

class TestPriceTierPriority:
    """30만 이상이면 카테고리 마진이 아닌 가격대 마진 적용."""

    def test_bag_over_300000_uses_tier(self):
        """30만 이상 가방 → +100,000 (not +50,000)."""
        margin = calculate_margin(300000, "bag", MARGIN_CONFIG)
        assert margin == 100000
        assert margin != 50000

    def test_outer_over_500000_uses_tier(self):
        """50만 이상 아우터 → +150,000 (not +40,000)."""
        margin = calculate_margin(500000, "outer", MARGIN_CONFIG)
        assert margin == 150000
        assert margin != 40000

    def test_etc_over_1000000_uses_tier(self):
        """100만 이상 기타 → +200,000 (not +30,000)."""
        margin = calculate_margin(1000000, "etc", MARGIN_CONFIG)
        assert margin == 200000
        assert margin != 30000


# ── 경계값 테스트 ──

class TestBoundaryValues:
    """정확히 경계 가격."""

    def test_exactly_300000(self):
        margin = calculate_margin(300000, "etc", MARGIN_CONFIG)
        assert margin == 100000

    def test_exactly_500000(self):
        margin = calculate_margin(500000, "etc", MARGIN_CONFIG)
        assert margin == 150000

    def test_exactly_1000000(self):
        margin = calculate_margin(1000000, "etc", MARGIN_CONFIG)
        assert margin == 200000

    def test_just_below_300000(self):
        margin = calculate_margin(299000, "etc", MARGIN_CONFIG)
        assert margin == 30000

    def test_just_below_500000(self):
        margin = calculate_margin(499000, "bag", MARGIN_CONFIG)
        assert margin == 100000


# ── calculate_sell_price 통합 ──

class TestCalculateSellPrice:
    """sell_price = cost_price + margin 확인."""

    def test_sell_price_bag_cheap(self):
        sell, margin = calculate_sell_price(200000, "bag", MARGIN_CONFIG)
        assert sell == 250000
        assert margin == 50000

    def test_sell_price_over_tier(self):
        sell, margin = calculate_sell_price(1000000, "etc", MARGIN_CONFIG)
        assert sell == 1200000
        assert margin == 200000

    def test_sell_price_wallet(self):
        sell, margin = calculate_sell_price(120000, "wallet", MARGIN_CONFIG)
        assert sell == 150000
        assert margin == 30000

    def test_sell_price_shoes(self):
        sell, margin = calculate_sell_price(90000, "shoes", MARGIN_CONFIG)
        assert sell == 120000
        assert margin == 30000


# ── classify_category 테스트 ──

class TestClassifyCategory:
    """상품명 기반 카테고리 분류."""

    def test_bag_by_keyword(self):
        cat = classify_category("구찌 토트백", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "bag"

    def test_watch_by_keyword(self):
        cat = classify_category("클래식 시계", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "watch"

    def test_outer_by_keyword(self):
        cat = classify_category("윈드 바람막이", "의류천국22", CATEGORY_KEYWORDS)
        assert cat == "outer"

    def test_top_by_keyword(self):
        cat = classify_category("스톤 맨투맨", "의류천국22", CATEGORY_KEYWORDS)
        assert cat == "top"

    def test_bottom_by_keyword(self):
        cat = classify_category("데님 슬랙스", "의류천국22", CATEGORY_KEYWORDS)
        assert cat == "bottom"

    def test_accessory_by_keyword(self):
        cat = classify_category("가죽 벨트", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "accessory"

    def test_wallet_by_keyword(self):
        """지갑 → wallet (not accessory)."""
        cat = classify_category("구찌 카드지갑", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "wallet"

    def test_wallet_keyholder(self):
        """키홀더 → wallet."""
        cat = classify_category("프라다 키홀더", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "wallet"

    def test_shoes_sneakers(self):
        """스니커즈 → shoes (not accessory)."""
        cat = classify_category("나이키 스니커즈", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "shoes"

    def test_shoes_loafer(self):
        """로퍼 → shoes."""
        cat = classify_category("구찌 로퍼", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "shoes"

    def test_shoes_boots(self):
        """부츠 → shoes."""
        cat = classify_category("프라다 부츠", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "shoes"

    def test_golf_jacket_goes_to_outer(self):
        """골프 자켓 → outer (골프 카테고리 삭제됨)."""
        cat = classify_category("파리게이츠 자켓", "의류천국22", CATEGORY_KEYWORDS)
        assert cat == "outer"

    def test_golf_polo_goes_to_top(self):
        """골프 폴로 → 상품 유형에 따라 분류 (골프 별도 카테고리 없음)."""
        cat = classify_category("파리게이츠 폴로", "의류천국22", CATEGORY_KEYWORDS)
        assert cat == "top"

    def test_golf_brand_tag_ignored(self):
        """golf_brand_tags는 무시됨 — 상품명 키워드로만 분류."""
        cat = classify_category(
            "폴로 셔츠", "의류천국22", CATEGORY_KEYWORDS,
            brand_tag="#PG", golf_brand_tags=["#PG"],
        )
        assert cat == "top"  # 셔츠 → top (골프로 가지 않음)

    def test_unknown_falls_to_etc(self):
        cat = classify_category("뭔지모를상품", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "etc"

    def test_wallet_priority_over_accessory(self):
        """wallet이 accessory보다 우선순위 높음."""
        cat = classify_category("지갑", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "wallet"

    def test_shoes_priority_over_accessory(self):
        """shoes가 accessory보다 우선순위 높음."""
        cat = classify_category("신발", "잡화천국22", CATEGORY_KEYWORDS)
        assert cat == "shoes"


# ── classify_gender 테스트 ──

class TestClassifyGender:
    """사이즈 기반 성별 분류."""

    def test_female_sizes(self):
        gender = classify_gender(["44", "55", "66"], GENDER_CONFIG)
        assert gender == "female"

    def test_male_sizes_numeric(self):
        gender = classify_gender(["95", "100", "105"], GENDER_CONFIG)
        assert gender == "male"

    def test_male_sizes_waist(self):
        gender = classify_gender(["30", "32", "34"], GENDER_CONFIG)
        assert gender == "male"

    def test_empty_sizes_default_male(self):
        gender = classify_gender([], GENDER_CONFIG)
        assert gender == "male"

    def test_mixed_sizes_default_male(self):
        """여성+남성 사이즈 혼합 → 남녀공용 → 남성."""
        gender = classify_gender(["44", "95"], GENDER_CONFIG)
        assert gender == "male"

    def test_unrecognized_sizes_default_male(self):
        """인식 불가 사이즈 → 기본값 male."""
        gender = classify_gender(["XS", "S", "M"], GENDER_CONFIG)
        assert gender == "male"

    def test_single_female_size(self):
        gender = classify_gender(["55"], GENDER_CONFIG)
        assert gender == "female"

    def test_female_keyword_women(self):
        """상품명에 WOMEN → female."""
        gender = classify_gender([], GENDER_CONFIG, product_name="나이키 WOMEN 자켓")
        assert gender == "female"

    def test_female_keyword_korean(self):
        """상품명에 우먼 → female."""
        gender = classify_gender([], GENDER_CONFIG, product_name="구찌 우먼 셔츠")
        assert gender == "female"

    def test_female_keyword_priority_over_size(self):
        """키워드가 사이즈보다 우선."""
        gender = classify_gender(["95", "100"], GENDER_CONFIG, product_name="프라다 우먼 코트")
        assert gender == "female"

    def test_male_keyword(self):
        """상품명에 맨즈 → male."""
        gender = classify_gender(["44", "55"], GENDER_CONFIG, product_name="버버리 맨즈 셔츠")
        assert gender == "male"

    def test_no_keyword_falls_to_size(self):
        """키워드 없으면 사이즈로 판단."""
        gender = classify_gender(["44", "55"], GENDER_CONFIG, product_name="프라다 코트")
        assert gender == "female"
