import logging

logger = logging.getLogger(__name__)

# ── 상품 카테고리 9종 ──
# bag / watch / wallet / shoes / outer / top / bottom / set / accessory / etc
# Task 9: "set"은 세트 상품(상의+하의 동시 판매) 전용 카테고리.
# 분류 조건:
#   1) set_part="top"/"bottom" 인 경우 (parse_set_product 로 분리된 상품)
#   2) 의류천국22 + category_keywords.set 매칭 (상하세트/상하의 등)
# 고객이 WC에서 직접 상의/하의로 이동하는 반자동 방식.

# category_keywords 검색 우선순위
_CATEGORY_PRIORITY = [
    "bag",
    "watch",
    "wallet",     # accessory보다 먼저 (지갑이 악세사리로 빠지지 않도록)
    "shoes",      # accessory보다 먼저
    "outer",
    "top",
    "bottom",
    "set",        # bottom 뒤, accessory 앞
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
    "#CT",  # 까르띠에 (Task 9: audit 후 추가 - 산토스/탱크 모델명은 brand fallback)
    "#SM",  # 쇼메
])


# outer 전용 브랜드 (다운 패딩 중심) - 의류천국22에서 키워드 매칭 실패 시 폴백
# Task 9 v3: 몽클레르 다운 모델명(레페/케랄레 등)은 상품명에 outer 키워드가 없어서 etc로 감
_OUTER_BRAND_TAGS = frozenset([
    "#MC",  # 몽클레르
])


# 의류천국22 전용 실측치 기반 분류 보조로직에서 사용하는 키워드
# 상의/하의/아우터 구분은 밴드 본문 내 실측 스펙 키워드로 역추론한다.
_SIZE_SPEC_BOTTOM_MARKERS = ("허리", "허벅지", "밑위")
_SIZE_SPEC_TOP_MARKERS = ("어깨", "가슴")
_SIZE_SPEC_SLEEVE_MARKERS = ("소매", "기장")


def _classify_by_size_spec(raw_content: str) -> str | None:
    """
    의류천국22 전용 -> 키워드 매칭 실패 시 raw 본문의 실측 스펙으로 카테고리 추정.

    판정 우선순위:
      1) 하의 -> "허리/허벅지/밑위" 중 1개 이상 등장 (bottom 특이성 100%)
      2) 상의 -> "어깨" AND "가슴" 둘 다 등장 (top 특이성 99%)
      3) 그 외 -> None (상위 로직으로 위임)

    실데이터 검증 결과 (의류천국22 493건 기준):
      - bottom 카테고리의 "허리" 출현율 24.4% vs top/outer는 0%
      - top 카테고리의 "어깨+가슴" 동시 출현율 18.8% vs outer는 1.6%
      - etc 84건 중 "어깨+가슴" 보유 24건 -> 대부분 실제 top

    Args:
        raw_content: 게시글 원본 텍스트 (ParsedProduct.raw_content 사용 전 원본)

    Returns:
        "bottom" / "top" / None
    """
    if not raw_content:
        return None

    # 1) 하의 판정 - 허리/허벅지/밑위 키워드 존재 (bottom 배타적 키워드)
    if any(marker in raw_content for marker in _SIZE_SPEC_BOTTOM_MARKERS):
        return "bottom"

    # 2) 상의 판정 - "어깨" AND "가슴" 둘 다 존재 (단독은 오탐 가능성 있음)
    has_shoulder = "어깨" in raw_content
    has_chest = "가슴" in raw_content
    if has_shoulder and has_chest:
        return "top"

    return None


def classify_category(
    product_name: str,
    source_band: str,
    category_keywords: dict,
    brand_tag: str = "",
    golf_brand_tags: list | None = None,
    keyword_exclusions: dict | None = None,
    raw_content: str = "",
    set_part: str | None = None,
) -> str:
    """
    상품명 기반으로 카테고리 분류 — **last-match 방식**.

    명품 상품명은 "수식어(소재/라인) + 상품유형" 구조이므로,
    상품명에서 가장 뒤에 위치한 키워드의 카테고리를 반환한다.

    예:
      "캐비어 스니커즈" → 스니커즈(shoes)가 뒤 → shoes
      "백 로* 반팔 티셔츠" → 티셔츠(top)가 뒤 → top
      "데님 볼캡 모자" → 모자(accessory)가 뒤 → accessory

    동일 위치에 여러 키워드가 매칭될 경우 _CATEGORY_PRIORITY 순서가 타이브레이커.

    Task 9: set_part가 "top" 또는 "bottom"이면 무조건 "set" 반환.
    세트 상품은 WC에서 반자동으로 상의/하의로 이동 처리한다.

    Fallback 체인 (키워드 매칭 실패 시):
      1) 시계 전용 브랜드 태그면 watch
      2) source_band이 "의류천국22"이고 raw_content 제공되면
         실측 스펙 키워드(허리/허벅지 or 어깨+가슴)로 bottom/top 추정
      3) 모두 실패 시 etc

    Args:
        raw_content: 게시글 원본 텍스트 (의류천국22 실측치 분류에 사용). 미제공 시 skip.
        set_part: "top"/"bottom"/None. None이 아니면 "set" 카테고리로 강제 분류.
        keyword_exclusions: 하위 호환성을 위해 파라미터 유지하지만 무시됨.
        golf_brand_tags: 하위 호환성을 위해 파라미터 유지하지만 무시됨.
    """
    # Task 9: 세트 상품은 set_part 값 기반으로 set 카테고리로 강제 분류
    # (parse_set_product 가 생성하는 상품만 set_part 값을 가짐)
    if set_part in ("top", "bottom"):
        return "set"

    text = product_name.lower()

    best_pos = -1
    best_cat = None
    best_priority = len(_CATEGORY_PRIORITY)  # 높을수록 낮은 우선순위

    for priority_idx, cat_key in enumerate(_CATEGORY_PRIORITY):
        keywords = category_keywords.get(cat_key, [])
        for kw in keywords:
            kw_lower = kw.lower()
            pos = text.rfind(kw_lower)
            if pos == -1:
                continue
            # 가장 뒤에 위치한 키워드 우선, 동일 위치면 _CATEGORY_PRIORITY 순서
            if pos > best_pos or (pos == best_pos and priority_idx < best_priority):
                best_pos = pos
                best_cat = cat_key
                best_priority = priority_idx

    if best_cat is not None:
        # Task 9: set 카테고리는 의류천국22 밴드에서만 유효
        # 잡화천국22에서 "상하세트" 같은 키워드가 매칭되면 (매우 드문 케이스) set 무시
        if best_cat == "set" and source_band != "의류천국22":
            # set 키워드 매칭 제외하고 다시 탐색
            best_pos = -1
            best_cat = None
            best_priority = len(_CATEGORY_PRIORITY)
            for priority_idx, cat_key in enumerate(_CATEGORY_PRIORITY):
                if cat_key == "set":
                    continue
                keywords = category_keywords.get(cat_key, [])
                for kw in keywords:
                    kw_lower = kw.lower()
                    pos = text.rfind(kw_lower)
                    if pos == -1:
                        continue
                    if pos > best_pos or (pos == best_pos and priority_idx < best_priority):
                        best_pos = pos
                        best_cat = cat_key
                        best_priority = priority_idx
            if best_cat is not None:
                return best_cat
            # 재탐색 실패 시 아래 fallback 체인으로
        else:
            return best_cat

    # 브랜드 기반 폴백: 시계 전용 브랜드는 키워드 없어도 watch
    if brand_tag in _WATCH_BRAND_TAGS:
        return "watch"

    # 브랜드 기반 폴백: outer 전용 브랜드 (몽클레르) + 의류천국22
    # 상품명이 모델명만 있고 outer 키워드 없는 경우 (레페/케랄레 등)
    if brand_tag in _OUTER_BRAND_TAGS and source_band == "의류천국22":
        return "outer"

    # 의류천국22 전용 폴백: 실측 스펙 키워드로 상의/하의 역추론
    if source_band == "의류천국22" and raw_content:
        spec_cat = _classify_by_size_spec(raw_content)
        if spec_cat is not None:
            return spec_cat

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
