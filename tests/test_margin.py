#!/usr/bin/env python3
"""Margin Engine 단위 테스트."""

import pytest

from src.margin_engine import calculate_margin, calculate_sell_price, classify_category

MARGIN_CONFIG = {
    "price_tiers": [
        {"min_price": 1000000, "margin": 200000},
        {"min_price": 500000, "margin": 150000},
        {"min_price": 300000, "margin": 100000},
    ],
    "category_margins": {
        "bag_watch": 54000,
        "outer": 44000,
        "etc": 34000,
    },
}

CATEGORY_KEYWORDS = {
    "bag_watch": ["가방", "백", "시계", "토트", "숄더백", "크로스백", "클러치", "워치", "핸드백", "배낭", "백팩"],
    "outer": ["자켓", "블루종", "패딩", "코트", "바람막이", "점퍼", "아우터", "윈드", "후드집업", "야상", "무스탕", "트렌치", "베스트"],
}


# ── 가격대별 마진 테스트 ──

class TestPriceTierMargin:
    """가격대별 마진 (30만/50만/100만 이상)."""

    def test_over_1000000(self):
        margin = calculate_margin(1200000, "bag_watch", MARGIN_CONFIG)
        assert margin == 200000

    def test_over_500000(self):
        margin = calculate_margin(550000, "bag_watch", MARGIN_CONFIG)
        assert margin == 150000

    def test_over_300000(self):
        margin = calculate_margin(350000, "outer", MARGIN_CONFIG)
        assert margin == 100000

    def test_under_300000_falls_to_category(self):
        margin = calculate_margin(250000, "bag_watch", MARGIN_CONFIG)
        assert margin == 54000


# ── 카테고리별 마진 테스트 ──

class TestCategoryMargin:
    """카테고리별 마진 (30만 미만일 때)."""

    def test_bag_watch(self):
        margin = calculate_margin(200000, "bag_watch", MARGIN_CONFIG)
        assert margin == 54000

    def test_outer(self):
        margin = calculate_margin(200000, "outer", MARGIN_CONFIG)
        assert margin == 44000

    def test_etc(self):
        margin = calculate_margin(200000, "etc", MARGIN_CONFIG)
        assert margin == 34000


# ── 가격대 우선순위 테스트 ──

class TestPriceTierPriority:
    """30만 이상이면 카테고리 마진이 아닌 가격대 마진 적용."""

    def test_bag_over_300000_uses_tier(self):
        """30만 이상 가방 → +100,000 (not +54,000)."""
        margin = calculate_margin(300000, "bag_watch", MARGIN_CONFIG)
        assert margin == 100000
        assert margin != 54000

    def test_outer_over_500000_uses_tier(self):
        """50만 이상 아우터 → +150,000 (not +44,000)."""
        margin = calculate_margin(500000, "outer", MARGIN_CONFIG)
        assert margin == 150000
        assert margin != 44000

    def test_etc_over_1000000_uses_tier(self):
        """100만 이상 기타 → +200,000 (not +34,000)."""
        margin = calculate_margin(1000000, "etc", MARGIN_CONFIG)
        assert margin == 200000
        assert margin != 34000


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
        assert margin == 34000

    def test_just_below_500000(self):
        margin = calculate_margin(499000, "bag_watch", MARGIN_CONFIG)
        assert margin == 100000


# ── calculate_sell_price 통합 ──

class TestCalculateSellPrice:
    """sell_price = cost_price + margin 확인."""

    def test_sell_price_bag_cheap(self):
        sell, margin = calculate_sell_price(200000, "bag_watch", MARGIN_CONFIG)
        assert sell == 254000
        assert margin == 54000

    def test_sell_price_over_tier(self):
        sell, margin = calculate_sell_price(1000000, "etc", MARGIN_CONFIG)
        assert sell == 1200000
        assert margin == 200000
