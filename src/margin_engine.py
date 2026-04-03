import logging

logger = logging.getLogger(__name__)


def classify_category(product_name: str, source_band: str, category_keywords: dict) -> str:
    text = product_name.lower()

    for kw in category_keywords.get("bag_watch", []):
        if kw in text:
            return "bag_watch"

    for kw in category_keywords.get("outer", []):
        if kw in text:
            return "outer"

    return "etc"


def calculate_margin(cost_price: int, category: str, margin_config: dict) -> int:
    for tier in margin_config["price_tiers"]:
        if cost_price >= tier["min_price"]:
            return tier["margin"]

    return margin_config["category_margins"].get(category, margin_config["category_margins"]["etc"])


def calculate_sell_price(cost_price: int, category: str, margin_config: dict) -> tuple[int, int]:
    margin = calculate_margin(cost_price, category, margin_config)
    return cost_price + margin, margin


if __name__ == "__main__":
    from src.config import load_config
    config = load_config()
    margin_config = config["margin"]
    keywords = config["category_keywords"]

    test_cases = [
        ("구찌 토트백", "잡화천국22", 250000),
        ("로* 윈드배색바람막이", "의류천국22", 50000),
        ("캐시미어 머플러", "잡화천국22", 80000),
        ("롱패딩 코트", "의류천국22", 350000),
        ("클래식 시계", "잡화천국22", 1200000),
        ("레더 숄더백", "잡화천국22", 550000),
    ]

    print("=== 마진 엔진 테스트 ===\n")
    for name, band, cost in test_cases:
        category = classify_category(name, band, keywords)
        sell, margin = calculate_sell_price(cost, category, margin_config)
        print(f"  {name}")
        print(f"    카테고리: {category} | 원가: {cost:,}원 | 마진: +{margin:,}원 | 판매가: {sell:,}원")
        print()

    print("margin_engine.py 정상 동작!")
