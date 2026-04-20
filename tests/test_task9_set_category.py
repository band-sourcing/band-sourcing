"""
[Task 9] 세트 카테고리 분류 로직 테스트.

- classify_category: set_part 기반 강제 분류
- settings.yaml: set 카테고리 키워드/마진/WC/duplicate 검증
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.content_parser import parse_post
from src.margin_engine import (
    _CATEGORY_PRIORITY,
    calculate_margin,
    calculate_sell_price,
    classify_category,
)


BRAND_MAP = {
    "#NK": "나이키",
    "#AZ": "어메이징크리",
    "#MC": "몽클",
    "#PD": "프라다",
}

CATEGORY_KEYWORDS = {
    "bag": ["가방", "백", "크로스백"],
    "watch": ["시계"],
    "wallet": ["지갑"],
    "shoes": ["스니커즈"],
    "outer": ["자켓", "코트"],
    "top": ["반팔", "티셔츠", "맨투맨", "셋업"],
    "bottom": ["팬츠", "데님"],
    "set": ["상하세트", "상하의", "상하 세트"],
    "accessory": ["벨트", "모자", "양말"],
}


# ═══════════════════════════════════════════════════════════════
# SECTION 1: _CATEGORY_PRIORITY 순서 검증
# ═══════════════════════════════════════════════════════════════
class TestCategoryPriorityOrder:
    def test_set_in_priority_list(self):
        """set은 키워드 매칭과 set_part 둘 다 지원하므로 _CATEGORY_PRIORITY에 포함."""
        assert "set" in _CATEGORY_PRIORITY

    def test_set_after_bottom(self):
        bottom_idx = _CATEGORY_PRIORITY.index("bottom")
        set_idx = _CATEGORY_PRIORITY.index("set")
        assert set_idx == bottom_idx + 1

    def test_set_before_accessory(self):
        set_idx = _CATEGORY_PRIORITY.index("set")
        accessory_idx = _CATEGORY_PRIORITY.index("accessory")
        assert set_idx < accessory_idx


# ═══════════════════════════════════════════════════════════════
# SECTION 2: classify_category - set_part 기반 강제 분류
# ═══════════════════════════════════════════════════════════════
class TestClassifyCategoryWithSetPart:
    def test_set_part_top_returns_set(self):
        cat = classify_category(
            "네오테크 후디 셋업 - 상의",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#AZ",
            set_part="top",
        )
        assert cat == "set"

    def test_set_part_bottom_returns_set(self):
        cat = classify_category(
            "네오테크 후디 셋업 - 하의",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#AZ",
            set_part="bottom",
        )
        assert cat == "set"

    def test_set_part_none_no_set_classification(self):
        """set_part가 None이면 일반 상품 -> 기존 분류 로직 작동."""
        cat = classify_category(
            "러브기마 반팔",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#BB",
            set_part=None,
        )
        assert cat == "top"

    def test_set_part_none_single_setup_product_goes_to_top(self):
        """상품명에 '셋업' 있지만 set_part가 None이면 top 카테고리 (단일 상품).
        예: "실버바클 셋업" 같은 단일 세트업 상품은 top으로."""
        cat = classify_category(
            "실버바클 셋업",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#AZ",
            set_part=None,
        )
        assert cat == "top"


# ═══════════════════════════════════════════════════════════════
# SECTION 2-B: 의류천국22 set 키워드 기반 분류 (set_part 없어도)
# ═══════════════════════════════════════════════════════════════
class TestSetKeywordBasedClassification:
    """parse_single_product 로 가는 상품이라도 의류천국22에서
    "상하세트"/"상하의" 키워드가 있으면 set 분류."""

    def test_상하세트_keyword_in_clothing_band(self):
        cat = classify_category(
            "[N] 메탈릭 상하세트",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#NK",
            set_part=None,
        )
        assert cat == "set"

    def test_상하의_keyword_in_clothing_band(self):
        cat = classify_category(
            "나이키 상하의 세트",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#NK",
            set_part=None,
        )
        assert cat == "set"

    def test_상하세트_keyword_in_accessory_band_not_set(self):
        """잡화천국22에서는 "상하세트" 키워드 매칭되어도 set이 아님."""
        # 실제로는 상하세트가 잡화 상품명에 올 확률은 거의 없지만 방어 로직 검증
        cat = classify_category(
            "남성 상하세트 가방",  # "가방"(bag) 매칭되므로 bag으로
            "잡화천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#GC",
            set_part=None,
        )
        # 잡화 밴드에서 set 키워드는 무시되고 다른 키워드로 분류
        assert cat == "bag"

    def test_set_keyword_fallback_to_etc_in_accessory_band(self):
        """잡화천국22에서 set 키워드만 있고 다른 키워드 없으면 etc."""
        cat = classify_category(
            "상하세트",
            "잡화천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#GC",
            set_part=None,
        )
        # 잡화 밴드에서 set은 무시 -> 다른 키워드 없음 -> etc
        assert cat == "etc"

    def test_false_positive_prevention_양말세트(self):
        """잡화천국22의 "양말 2켤레 세트"는 set 아님 (accessory).
        category_keywords.set에 '세트' 키워드가 없으므로 매칭 안 됨."""
        cat = classify_category(
            "로* 양말 2켤레 세트",
            "잡화천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#CN",
            set_part=None,
        )
        assert cat == "accessory"

    def test_false_positive_prevention_팬티세트(self):
        """잡화천국22의 "남성팬티 3종세트"도 set 아님 (팬티=accessory)."""
        kw = dict(CATEGORY_KEYWORDS)
        kw["accessory"] = kw["accessory"] + ["팬티"]
        cat = classify_category(
            "체크 기사도 남성팬티 3종세트",
            "잡화천국22",
            kw,
            brand_tag="#BB",
            set_part=None,
        )
        assert cat == "accessory"

    def test_set_part_top_overrides_other_keywords(self):
        """set_part가 있으면 상품명에 다른 카테고리 키워드 있어도 set 우선."""
        cat = classify_category(
            "체크 셔츠 - 상의",  # "셔츠"는 top 키워드
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#GC",
            set_part="top",
        )
        assert cat == "set"

    def test_invalid_set_part_falls_to_normal(self):
        """set_part가 유효하지 않은 값이면 일반 분류."""
        cat = classify_category(
            "러브기마 반팔",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#BB",
            set_part="invalid",
        )
        assert cat == "top"

    def test_default_set_part_backward_compat(self):
        """set_part 파라미터 미제공 시 기존 동작 유지."""
        cat = classify_category(
            "반팔 티셔츠",
            "의류천국22",
            CATEGORY_KEYWORDS,
            brand_tag="#BB",
        )
        assert cat == "top"


# ═══════════════════════════════════════════════════════════════
# SECTION 3: parse_set_product 통합 - 세트 상품 생성 + 분류
# ═══════════════════════════════════════════════════════════════
class TestSetProductIntegration:
    def test_set_product_generates_two_products_with_set_part(self):
        content = """#AZ
"네오테크 후디 셋업"
색상: 그레이
상의: 95 100 105
하의: 30 32 34
상의 053 (AL)
하의 046 (AL)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        assert len(products) == 2
        assert products[0].set_part == "top"
        assert products[1].set_part == "bottom"

    def test_set_product_classified_as_set(self):
        """parse_set_product가 만든 상품은 set_part로 set 분류됨."""
        content = """#AZ
"네오테크 후디 셋업"
색상: 그레이
상의: 95 100 105
하의: 30 32 34
상의 053 (AL)
하의 046 (AL)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")

        for product in products:
            cat = classify_category(
                product.product_name,
                product.source_band,
                CATEGORY_KEYWORDS,
                brand_tag=product.brand_tag,
                set_part=product.set_part,
            )
            assert cat == "set"

    def test_single_product_not_set(self):
        """단일 상품 (set_part=None)은 기존 카테고리로 분류됨."""
        content = """#NK
반팔 티셔츠
블랙 M L XL
028 (QT)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        assert len(products) == 1
        assert products[0].set_part is None

        cat = classify_category(
            products[0].product_name,
            products[0].source_band,
            CATEGORY_KEYWORDS,
            brand_tag=products[0].brand_tag,
            set_part=products[0].set_part,
        )
        assert cat == "top"


# ═══════════════════════════════════════════════════════════════
# SECTION 4: settings.yaml - 설정 검증
# ═══════════════════════════════════════════════════════════════
class TestSettingsYaml:
    @pytest.fixture
    def settings(self):
        settings_path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
        with settings_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_margin_has_set_category(self, settings):
        assert "set" in settings["margin"]["category_margins"]
        assert settings["margin"]["category_margins"]["set"] == 30000

    def test_category_keywords_has_set_with_clothing_only_keywords(self, settings):
        """set 키워드는 의류 전용으로 제한 (세트/셋트/set/셋업 제외)."""
        assert "set" in settings["category_keywords"]
        set_keywords = settings["category_keywords"]["set"]
        required = {"상하세트", "상하의"}
        assert required.issubset(set(set_keywords))
        # false positive 유발 가능성 있는 키워드는 set에 없어야 함
        assert "세트" not in set_keywords
        assert "set" not in set_keywords

    def test_top_keywords_still_have_셋업(self, settings):
        """단일 '셋업' 상품 (실버바클 셋업 등) 은 top으로 분류되도록 유지."""
        top_keywords = settings["category_keywords"]["top"]
        assert "셋업" in top_keywords

    def test_wc_categories_has_set(self, settings):
        assert "set" in settings["wc_categories"]
        assert isinstance(settings["wc_categories"]["set"], int)

    def test_duplicate_has_set_enabled(self, settings):
        assert "set" in settings["duplicate"]
        assert settings["duplicate"]["set"]["enabled"] is True


# ═══════════════════════════════════════════════════════════════
# SECTION 4-B: is_set_product 완화 검증
# ═══════════════════════════════════════════════════════════════
class TestIsSetProductLoosened:
    def test_existing_top_bottom_lines_still_work(self):
        from src.content_parser import is_set_product
        content = """#AZ
"네오테크 후디 셋업"
상의: 95 100 105
하의: 30 32 34
상의 053 (AL)
하의 046 (AL)"""
        assert is_set_product(content) is True

    def test_상하세트_with_multiple_prices(self):
        """상하세트 키워드 + 가격 코드 2개"""
        from src.content_parser import is_set_product
        content = """#NK
메탈릭 상하세트
상하 048 (AL)
하의 030 (AL)"""
        assert is_set_product(content) is True

    def test_상하의_with_multiple_prices(self):
        from src.content_parser import is_set_product
        content = """#NK
나이키 상하의 세트
048 (AL)
030 (AL)"""
        assert is_set_product(content) is True

    def test_size_pattern_rule_top_and_bottom_sizes(self):
        """사용자 규칙: 상의+95/100/105/110 AND 하의+30/32/34/36."""
        from src.content_parser import is_set_product
        content = """#GC
SALE
실크 셔츠 & 픽셀 쇼츠
색상 : 레드
사이즈 : 상의 95(XS), 100(S), 110(L)
사이즈 : 하의 30(XS), 32(S), 34(M)
048 (AL)"""
        assert is_set_product(content) is True

    def test_size_pattern_rule_size_measurement_in_content(self):
        """상의 63 / 57 / 23 / 79 + 하의 27 / 60 같은 실측."""
        from src.content_parser import is_set_product
        content = """#NK
[N] 오서라이즈 세트
가슴 / 어깨 / 팔 / 총장
상의 100 / 57 / 23 / 79
허리 / 총장
하의 30 / 60
048 (AL)"""
        assert is_set_product(content) is True

    def test_size_pattern_only_bottom_not_set(self):
        """하의만 있고 상의 사이즈 없으면 False - 단일 바지 상품."""
        from src.content_parser import is_set_product
        content = """A*O
하이웨이스트 바이커 쇼츠
색상 - 블랙
사이즈 - S, M, L
S: 허리26 허벅지21 밑위22 총장36
041 (QI)"""
        assert is_set_product(content) is False

    def test_no_set_markers_false(self):
        from src.content_parser import is_set_product
        content = """#PD
나일론 재킷
088 (QT)"""
        assert is_set_product(content) is False


# ═══════════════════════════════════════════════════════════════
# SECTION 4-C: parse_set_product 단일 가격 폴백
# ═══════════════════════════════════════════════════════════════
class TestParseSetProductSinglePriceFallback:
    def test_set_with_one_price_returns_single_product(self):
        """세트지만 가격 1개 -> 단일 세트 상품 (set_part="top")."""
        from src.content_parser import parse_post
        content = """#NK
[N] 오서라이즈 세트
상의 63 57 23
하의 27 60
048 (AL)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        assert len(products) == 1
        assert products[0].set_part == "top"  # set 분류 트리거용
        assert products[0].cost_price == 48000

    def test_set_single_price_classified_as_set(self):
        """단일 가격 세트도 classify_category에서 set으로 분류됨."""
        from src.content_parser import parse_post
        content = """#NK
[N] 오서라이즈 세트
상의 100 57
하의 30 60
048 (AL)"""
        products = parse_post(content, BRAND_MAP, "의류천국22")
        p = products[0]
        cat = classify_category(
            p.product_name, p.source_band, CATEGORY_KEYWORDS,
            brand_tag=p.brand_tag, raw_content=content, set_part=p.set_part,
        )
        assert cat == "set"


# ═══════════════════════════════════════════════════════════════
# SECTION 5: calculate_margin / calculate_sell_price
# ═══════════════════════════════════════════════════════════════
class TestSetMargin:
    @pytest.fixture
    def margin_config(self):
        return {
            "price_tiers": [
                {"min_price": 1000000, "margin": 200000},
                {"min_price": 500000, "margin": 150000},
                {"min_price": 300000, "margin": 100000},
            ],
            "category_margins": {
                "bag": 50000,
                "top": 30000,
                "bottom": 30000,
                "set": 30000,
                "etc": 30000,
            },
        }

    def test_set_margin_30000(self, margin_config):
        margin = calculate_margin(50000, "set", margin_config)
        assert margin == 30000

    def test_set_sell_price(self, margin_config):
        sell, margin = calculate_sell_price(50000, "set", margin_config)
        assert margin == 30000
        assert sell == 80000

    def test_set_high_price_tier_margin(self, margin_config):
        """세트 상품 고가여도 price_tier가 우선."""
        margin = calculate_margin(600000, "set", margin_config)
        assert margin == 150000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
