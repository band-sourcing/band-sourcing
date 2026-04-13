import logging

logger = logging.getLogger(__name__)

# ── 상품 카테고리 8종 ──
# bag / watch / wallet / shoes / outer / top / bottom / accessory / etc

# category_keywords 검색 우선순위
_CATEGORY_PRIORITY = [
    "bag",
    "watch",
    "wallet",     # accessory보다 먼저 (지갑이 악세사리로 빠지지 않도록)
    "shoes",      # accessory보다 먼저
    "outer",
    "top",
    "bottom",
    "accessory",
]


# 키워드 매칭 실패 시 브랜드 기반 폴백 (시계 전용 브랜드)
_WATCH_BRAND_TAGS = frozenset([
    "#RL",  # 로렉스
    "#OM",  # 오메가
    "#AP",  # 오데마피게
    "#HB",  # 위블로
    "#BR",  # 브라이틀링
    "#IW",  # IWC
    "#PK",  # 파텍필립
    "#VR",  # 바쉐론콘스탄틴
    "#BU",  # 브레게
    "#RM",  # 리차드밀
    "#PI",  # 피아제
    "#UN",  # 올리스나르덴
    "#FK",  # 프랭크뮬러
    "#RU",  # 로저드뷔
    "#JE",  # 예거르쿨트르
    "#TH",  # 태그호이어
    "#CP",  # 쇼파드
    "#SM",  # 쇼메
])


def classify_category(
    product_name: str,
    source_band: str,
    category_keywords: dict,
    brand_tag: str = "",
    golf_brand_tags: list | None = None,
) -> str:
    """
    상품명 기반으로 카테고리 분류.

    분류 우선순위:
    1. category_keywords에서 키워드 매칭 (우선순위: bag > watch > wallet > shoes > outer > top > bottom > accessory)
    2. 브랜드 기반 폴백 (시계 브랜드 → watch)
    3. 매칭 없으면 → etc

    Note: golf_brand_tags 파라미터는 하위 호환성을 위해 유지하지만 무시됨.
    """
    text = product_name.lower()
    for cat_key in _CATEGORY_PRIORITY:
        keywords = category_keywords.get(cat_key, [])
        for kw in keywords:
            if kw.lower() in text:
                return cat_key

    # 브랜드 기반 폴백: 시계 전용 브랜드는 키워드 없어도 watch
    if brand_tag in _WATCH_BRAND_TAGS:
        return "watch"

    return "etc"


def calculate_margin(cost_price: int, category: str, margin_config: dict) -> int:
    """
    마진 계산.
    1) 가격대 마진이 우선 (30만/50만/100만 이상)
    2) 없으면 카테고리 마진 직접 조회
    """
    # 가격대 마진 체크
    for tier in margin_config["price_tiers"]:
        if cost_price >= tier["min_price"]:
            return tier["margin"]

    # 카테고리 마진 직접 조회
    return margin_config["category_margins"].get(
        category, margin_config["category_margins"].get("etc", 30000)
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
        ("구찌 카드지갑", "잡화천국22", 120000, "#GC", []),
        ("나이키 로퍼", "잡화천국22", 90000, "#NK", []),
    ]

    print("=== 마진 엔진 테스트 (v2) ===\n")
    for name, band, cost, tag, sizes in test_cases:
        category = classify_category(name, band, keywords)
        sell, margin = calculate_sell_price(cost, category, margin_config)
        gender = classify_gender(sizes, gender_conf)
        print(f"  {name}")
        print(f"    카테고리: {category} | 성별: {gender} | 원가: {cost:,}원 | 마진: +{margin:,}원 | 판매가: {sell:,}원")
        print()

    print("margin_engine.py 정상 동작!")
