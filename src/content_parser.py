import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ParseError(Exception):
    pass


@dataclass
class ParsedProduct:
    brand_tag: str
    brand_name_en: str
    product_name: str
    colors: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    measurements: str | None = None
    cost_price: int = 0
    season_code: str = ""
    set_part: str | None = None
    source_band: str = ""
    raw_content: str = ""


def preprocess_content(raw_content: str) -> str:
    text = re.sub(r'<band:hashtag>(.*?)</band:hashtag>', r'\1', raw_content)
    text = re.sub(r'<band:refer[^>]*>(.*?)</band:refer>', r'\1', text)
    text = re.sub(r'<band:attachment[^/]*/>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def is_set_product(content: str) -> bool:
    has_top = bool(re.search(r'상의\s', content))
    has_bottom = bool(re.search(r'하의\s', content))
    return has_top and has_bottom


def format_product_name(brand_name_en: str, raw_name: str) -> str:
    return f"[{brand_name_en}] {raw_name}"


def _extract_brand(lines: list[str], brand_map: dict) -> tuple[str, str, int]:
    brand_pattern = re.compile(r'^(#[A-Za-z]{2,4})\b')
    for i, line in enumerate(lines):
        m = brand_pattern.match(line)
        if m:
            tag = m.group(1).upper()
            name_en = brand_map.get(tag, tag.replace('#', ''))
            return tag, name_en, i
    raise ParseError("브랜드 태그를 찾을 수 없음")


def _is_price_line(line: str) -> bool:
    skip_prefixes = ['상의', '하의', '사이즈', '색상']
    stripped = line.strip()
    for prefix in skip_prefixes:
        if stripped.startswith(prefix):
            return False
    return True


def _extract_price(lines: list[str]) -> list[tuple[int, str, str | None]]:
    price_pattern = re.compile(r'(\d{3})\s*\(([A-Za-z][A-Za-z0-9]*)\)')
    results = []
    for line in lines:
        m = price_pattern.search(line)
        if m and _is_price_line(line):
            price = int(m.group(1)) * 1000
            season = m.group(2)
            label = None
            if '상의' in line:
                label = 'top'
            elif '하의' in line:
                label = 'bottom'
            results.append((price, season, label))
    return results


def _extract_price_set(lines: list[str]) -> list[tuple[int, str, str | None]]:
    price_pattern = re.compile(r'^\s*(?:(상의|하의)\s+)?(\d{3})\s*\(([A-Za-z][A-Za-z0-9]*)\)\s*$')
    results = []
    for line in lines:
        m = price_pattern.match(line)
        if m:
            label_raw = m.group(1)
            price = int(m.group(2)) * 1000
            season = m.group(3)
            label = None
            if label_raw == '상의':
                label = 'top'
            elif label_raw == '하의':
                label = 'bottom'
            results.append((price, season, label))
    return results


def _extract_colors(lines: list[str]) -> list[str]:
    pattern = re.compile(r'색상\s*[-:]\s*(.+)')
    for line in lines:
        m = pattern.search(line)
        if m:
            raw = m.group(1)
            return [c.strip() for c in re.split(r'[,/]', raw) if c.strip()]
    return []


def _extract_sizes(lines: list[str]) -> list[str]:
    pattern = re.compile(r'사이즈\s*[-:]\s*(.+)')
    for line in lines:
        m = pattern.search(line)
        if m:
            raw = m.group(1)
            return [s.strip() for s in re.split(r'[,/]', raw) if s.strip()]
    return []


def _extract_measurements(lines: list[str]) -> str | None:
    pattern = re.compile(r'(총장\d+|가슴\d+|어깨\d+|소매\d+)')
    for line in lines:
        if pattern.search(line):
            return line.strip()
    return None


# ── 민감정보 제거 (소비자에게 노출하면 안 되는 내용) ──

# 가격코드 라인: "070 (QI)", "121 (AI24)", "상의 053 (AL)" 등
_PRICE_CODE_RE = re.compile(
    r'^\s*(?:상의|하의)?\s*\d{3}\s*\([A-Za-z][A-Za-z0-9]*\)\s*$'
)

# 브랜드 해시태그 단독 라인: "#FG", "#PD" 등
_BRAND_TAG_RE = re.compile(r'^\s*#[A-Za-z]{2,4}\s*$')

# 카카오톡/연락처 정보
_CONTACT_RE = re.compile(
    r'카카오톡|카톡|카톡채널|카카오.*채널|카카오.*문의|'
    r'카톡.*추가|카톡.*친구|친구추가|'
    r'톡문의|톡.*상담|채팅.*문의|'
    r'e\d{4}|'  # 카톡 ID 패턴 (e7132 등)
    r'010[-\s]?\d{4}[-\s]?\d{4}',  # 전화번호
    re.IGNORECASE
)

# 공장코드/등급 해시태그: "#SA급", "#고퀄", "#qe", "#bs" 등
_GRADE_TAG_RE = re.compile(
    r'#(?:SA급|고퀄|1:1|정품급|최상급|S급|A급|AA급|AAA급)',
    re.IGNORECASE
)

# 소싱 관련 정보 (국내배송 가격 등 - 원가 노출)
_SOURCING_INFO_RE = re.compile(
    r'국내배송.*?₩[\d,]+|'
    r'₩[\d,]+.*?국내배송|'
    r'해외배송.*?₩[\d,]+|'
    r'₩[\d,]+.*?해외배송',
    re.IGNORECASE
)


def _clean_raw_content(cleaned_text: str) -> str:
    """밴드 본문에서 민감정보를 제거한 소비자용 텍스트 생성."""
    lines = cleaned_text.split('\n')
    result = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 가격코드 라인 제거
        if _PRICE_CODE_RE.match(stripped):
            continue

        # 브랜드 해시태그 단독 라인 제거
        if _BRAND_TAG_RE.match(stripped):
            continue

        # 카카오톡/연락처 정보 포함 라인 제거
        if _CONTACT_RE.search(stripped):
            continue

        # 등급 해시태그 제거 (라인 전체가 아닌 태그만 제거)
        cleaned_line = _GRADE_TAG_RE.sub('', stripped)

        # 소싱 가격 정보 제거
        cleaned_line = _SOURCING_INFO_RE.sub('', cleaned_line)

        # 인라인 브랜드 해시태그 제거 (#LV, #FG 등이 다른 텍스트와 함께 있는 경우)
        cleaned_line = re.sub(r'#[A-Za-z]{2,4}\b', '', cleaned_line)

        # 정리
        cleaned_line = cleaned_line.strip()
        if cleaned_line:
            result.append(cleaned_line)

    return '\n'.join(result)


def parse_single_product(content: str, brand_map: dict) -> ParsedProduct:
    lines = [l.strip() for l in content.split('\n') if l.strip()]

    brand_tag, brand_name_en, brand_idx = _extract_brand(lines, brand_map)

    product_name = lines[brand_idx + 1] if brand_idx + 1 < len(lines) else ""

    prices = _extract_price(lines)
    if not prices:
        raise ParseError("가격코드를 찾을 수 없음")
    cost_price = prices[0][0]
    season_code = prices[0][1]

    return ParsedProduct(
        brand_tag=brand_tag,
        brand_name_en=brand_name_en,
        product_name=product_name,
        colors=_extract_colors(lines),
        sizes=_extract_sizes(lines),
        measurements=_extract_measurements(lines),
        cost_price=cost_price,
        season_code=season_code,
        set_part=None,
        source_band=""
    )


def parse_set_product(content: str, brand_map: dict) -> list[ParsedProduct]:
    lines = [l.strip() for l in content.split('\n') if l.strip()]

    brand_tag, brand_name_en, _ = _extract_brand(lines, brand_map)

    product_name = ""
    name_pattern = re.compile(r'"(.+?)"')
    for line in lines:
        m = name_pattern.search(line)
        if m:
            product_name = m.group(1)
            break

    if not product_name:
        _, _, brand_idx = _extract_brand(lines, brand_map)
        product_name = lines[brand_idx + 1] if brand_idx + 1 < len(lines) else ""

    colors = _extract_colors(lines)

    top_sizes = []
    bottom_sizes = []
    top_pattern = re.compile(r'상의\s*[-:]\s*(.+)')
    bottom_pattern = re.compile(r'하의\s*[-:]\s*(.+)')
    for line in lines:
        m = top_pattern.search(line)
        if m:
            top_sizes = [s.strip() for s in re.split(r'[,/]', m.group(1)) if s.strip()]
        m = bottom_pattern.search(line)
        if m:
            bottom_sizes = [s.strip() for s in re.split(r'[,/]', m.group(1)) if s.strip()]

    prices = _extract_price_set(lines)
    if len(prices) < 2:
        raise ParseError("세트 상품인데 가격이 2개 미만")

    top_price, top_season = None, ""
    bottom_price, bottom_season = None, ""

    for p_val, p_season, p_label in prices:
        if p_label == 'top' and top_price is None:
            top_price = p_val
            top_season = p_season
        elif p_label == 'bottom' and bottom_price is None:
            bottom_price = p_val
            bottom_season = p_season

    if top_price is None:
        top_price = prices[0][0]
        top_season = prices[0][1]
    if bottom_price is None:
        bottom_price = prices[1][0]
        bottom_season = prices[1][1]

    top_product = ParsedProduct(
        brand_tag=brand_tag,
        brand_name_en=brand_name_en,
        product_name=f"{product_name} - 상의",
        colors=colors,
        sizes=top_sizes,
        measurements=None,
        cost_price=top_price,
        season_code=top_season,
        set_part="top",
        source_band=""
    )

    bottom_product = ParsedProduct(
        brand_tag=brand_tag,
        brand_name_en=brand_name_en,
        product_name=f"{product_name} - 하의",
        colors=colors,
        sizes=bottom_sizes,
        measurements=None,
        cost_price=bottom_price,
        season_code=bottom_season,
        set_part="bottom",
        source_band=""
    )

    return [top_product, bottom_product]


def parse_post(content: str, brand_map: dict, source_band: str) -> list[ParsedProduct]:
    cleaned = preprocess_content(content)

    # 민감정보 제거한 소비자용 본문 생성
    consumer_text = _clean_raw_content(cleaned)

    if is_set_product(cleaned):
        products = parse_set_product(cleaned, brand_map)
    else:
        products = [parse_single_product(cleaned, brand_map)]

    for p in products:
        p.source_band = source_band
        p.raw_content = consumer_text

    return products


if __name__ == "__main__":
    brand_map = {"#PD": "PRADA", "#NK": "NIKE", "#AZ": "AMAZINGCORE"}

    # 일반 상품 테스트
    test1 = """#PD
아르케 리나일론 숄더
사이즈 : 22.0 x 18.0 x 6.0 cm
121 (AI24)"""

    result1 = parse_post(test1, brand_map, "잡화천국22")
    p = result1[0]
    print("=== 일반 상품 테스트 ===")
    print(f"  브랜드: {p.brand_tag} -> {p.brand_name_en}")
    print(f"  상품명: {format_product_name(p.brand_name_en, p.product_name)}")
    print(f"  원가: {p.cost_price:,}원")
    print(f"  시즌: {p.season_code}")
    print(f"  raw_content: [{p.raw_content}]")

    # 의류 테스트
    test2 = """#NK
로* 윈드배색바람막이
색상-블랙,화이트,그레이
사이즈-남여공용 FREE
총장72 가슴65
050 (BM)"""

    result2 = parse_post(test2, brand_map, "의류천국22")
    p = result2[0]
    print("\n=== 의류 상품 테스트 ===")
    print(f"  브랜드: {p.brand_tag} -> {p.brand_name_en}")
    print(f"  상품명: {format_product_name(p.brand_name_en, p.product_name)}")
    print(f"  색상: {p.colors}")
    print(f"  사이즈: {p.sizes}")
    print(f"  실측: {p.measurements}")
    print(f"  원가: {p.cost_price:,}원")
    print(f"  raw_content: [{p.raw_content}]")

    # 세트 상품 테스트
    test3 = """#AZ
"네오테크 후디 셋업"
색상: 그레이/ 블랙
상의: 95(M)/ 100(L)/ 105(XL)/ 110(XXL)
하의: 30(M)/ 32(L)/ 34(XL)/ 36(XXL)
상의 053 (AL)
하의 046 (AL)"""

    result3 = parse_post(test3, brand_map, "의류천국22")
    print("\n=== 세트 상품 테스트 ===")
    for p in result3:
        print(f"  {p.set_part}: {format_product_name(p.brand_name_en, p.product_name)} -> {p.cost_price:,}원")
    print(f"  raw_content: [{result3[0].raw_content}]")

    # 민감정보 제거 테스트
    test4 = """#LV
반돌리에 25
사이즈 : 25.0 x 19.0 x 15.0 cm
소재 : 모노그램 코팅 캔버스 / 카우하이드 가죽 트리밍
#SA급 #고퀄
국내배송(3~ 5일) ₩186,000
카카오톡 친구추가 : e7132
186 (QI)"""

    brand_map_ext = {**brand_map, "#LV": "LOUIS VUITTON"}
    result4 = parse_post(test4, brand_map_ext, "잡화천국22")
    p = result4[0]
    print("\n=== 민감정보 제거 테스트 ===")
    print(f"  상품명: {format_product_name(p.brand_name_en, p.product_name)}")
    print(f"  raw_content: [{p.raw_content}]")
    print(f"  가격코드 노출?: {'186 (QI)' in p.raw_content}")
    print(f"  카톡 노출?: {'카카오톡' in p.raw_content}")
    print(f"  등급태그 노출?: {'SA급' in p.raw_content}")

    print("\ncontent_parser.py 정상 동작!")
