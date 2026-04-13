import logging

logger = logging.getLogger(__name__)

# ── 상품 카테고리 8종 ──
# bag / watch / outer / top / bottom / accessory / golf / etc

# category_keywords 검색 우선순위 (설정 파일 키 순서)
_CATEGORY_PRIORITY = [
    "golf",       # 골프가 가장 먼저 (골프 자켓 → golf, not outer)
    "bag",
    "watch",
    "outer",
    "top",
    "bottom",
    "accessory",
]


def classify_category(
    product_name: str,
    source_band: str,
    category_keywords: dict,
    brand_tag: str = "",
    golf_brand_tags: list | None = None,
) -> str:
    """
    상품명 + 브랜드 태그 기반으로 8종 카테고리 분류.

    분류 우선순위:
    1. golf_brand_tags에 brand_tag가 있으면 → golf
    2. category_keywords에서 키워드 매칭 (우선순위: golf > bag > watch > outer > top > bottom > accessory)
    3. 매칭 없으면 → etc
    """
    # 1) 골프 브랜드 태그 체크
    if golf_brand_tags and brand_tag in golf_brand_tags:
        return "golf"

    # 2) 키워드 매칭
    text = product_name.lower()
    for cat_key in _CATEGORY_PRIORITY:
        keywords = category_keywords.get(cat_key, [])
        for kw in keywords:
            if kw.lower() in text:
                return cat_key

    return "etc"


def get_margin_category(category: str, margin_config: dict) -> str:
    """
    세분화된 카테고리(8종) → 마진 카테고리(3종) 변환.
    margin_category_map이 설정에 없으면 레거시 호환.
    """
    cat_map = margin_config.get("margin_category_map", {})
    if cat_map:
        return cat_map.get(category, "etc")

    # 레거시 호환: bag_watch / outer / etc
    if category in ("bag", "watch"):
        return "bag_watch"
    elif category == "outer":
        return "outer"
    return "etc"


def calculate_margin(cost_price: int, category: str, margin_config: dict) -> int:
    """
    마진 계산.
    1) 가격대 마진이 우선 (30만/50만/100만 이상)
    2) 없으면 카테고리 마진 적용
    """
    # 가격대 마진 체크
    for tier in margin_config["price_tiers"]:
        if cost_price >= tier["min_price"]:
            return tier["margin"]

    # 카테고리 마진
    margin_cat = get_margin_category(category, margin_config)
    return margin_config["category_margins"].get(
        margin_cat, margin_config["category_margins"]["etc"]
    )


def calculate_sell_price(cost_price: int, category: str, margin_config: dict) -> tuple[int, int]:
    margin = calculate_margin(cost_price, category, margin_config)
    return cost_price + margin, margin


# ── 성별 분류 ──

def classify_gender(sizes: list[str], gender_config: dict, product_name: str = "") -> str:
    """
    성별 분류. 우선순위:
    1. 상품명에 여성/남성 키워드 → 즉시 판정
    2. 사이즈 기반 (44/55/66/77=여 / 90/95/100+=남)
    3. 판별 불가 → default_gender (기본 male)
    """
    # 1) 상품명 키워드 체크
    if product_name:
        name_lower = product_name.lower()
        female_kw = gender_config.get("female_keywords", [])
        male_kw = gender_config.get("male_keywords", [])
        for kw in female_kw:
            if kw.lower() in name_lower:
                return "female"
        for kw in male_kw:
            if kw.lower() in name_lower:
                return "male"

    # 2) 사이즈 기반
    if not sizes:
        return gender_config.get("default_gender", "male")

    female_markers = set(gender_config.get("female_sizes", []))
    male_markers = set(gender_config.get("male_sizes", []))

    has_female = False
    has_male = False

    for size in sizes:
        s = size.strip()
        if s in female_markers:
            has_female = True
        if s in male_markers:
            has_male = True

    if has_female and not has_male:
        return "female"
    if has_male and not has_female:
        return "male"
    return gender_config.get("default_gender", "male")


if __name__ == "__main__":
    from src.config import load_config
    config = load_config()
    margin_config = config["margin"]
    keywords = config["category_keywords"]
    golf_tags = config.get("golf_brand_tags", [])
    gender_conf = config.get("gender_classification", {})

    test_cases = [
        ("구찌 토트백", "잡화천국22", 250000, "#GC", []),
        ("로* 윈드배색바람막이", "의류천국22", 50000, "#NK", []),
        ("캐시미어 머플러", "잡화천국22", 80000, "#HM", []),
        ("롱패딩 코트", "의류천국22", 350000, "#MC", []),
        ("클래식 시계", "잡화천국22", 1200000, "#OM", []),
        ("레더 숄더백", "잡화천국22", 550000, "#PD", []),
        ("파리게이츠 골프 폴로", "의류천국22", 80000, "#PG", ["44", "55"]),
        ("스톤아일랜드 맨투맨", "의류천국22", 90000, "#ST", ["95", "100", "105"]),
        ("막스마라 슬랙스", "의류천국22", 150000, "#MM", ["44", "55", "66"]),
        ("나이키 스니커즈", "잡화천국22", 60000, "#NK", []),
    ]

    print("=== 마진 엔진 테스트 (세분화) ===\n")
    for name, band, cost, tag, sizes in test_cases:
        category = classify_category(name, band, keywords, tag, golf_tags)
        sell, margin = calculate_sell_price(cost, category, margin_config)
        gender = classify_gender(sizes, gender_conf)
        print(f"  {name}")
        print(f"    카테고리: {category} | 성별: {gender} | 원가: {cost:,}원 | 마진: +{margin:,}원 | 판매가: {sell:,}원")
        print()

    print("margin_engine.py 정상 동작!")
