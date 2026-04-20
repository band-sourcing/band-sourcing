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
    """
    세트 상품(상의+하의 동시 판매) 여부 판정.

    인식 조건 (Task 9에서 완화됨):
      A) 전통: "상의 " + "하의 " 라인 동시 존재 (가격 분리 등록용)
      B) 명시: "상하세트"/"상하의" + 가격 2개 이상
      C) 사이즈 패턴 기반 (사용자 제시 규칙):
         - "상의" 키워드 근처에 상의 사이즈 (90/95/100/105/110 대) AND
         - "하의" 키워드 근처에 하의 사이즈 (30/32/34/36/38 대)

    SALE/세일 마커는 무시 (상품명에 섞여 있어도 상관없음).
    """
    # A) 전통 조건
    has_top_line = bool(re.search(r'상의\s', content))
    has_bottom_line = bool(re.search(r'하의\s', content))
    if has_top_line and has_bottom_line:
        return True

    # B) 명시 조건
    has_set_marker = bool(re.search(r'상하\s*세트|상하의|상하\b', content))
    if has_set_marker:
        price_count = len(re.findall(r'\d{3}\s*\([A-Za-z][A-Za-z0-9]*\)', content))
        if price_count >= 2:
            return True

    # C) 사이즈 패턴 기반 (사용자 규칙)
    # "상의" 또는 "상의 :" 또는 "🔘 사이즈 : 상의" 뒤에 95/100/105/110 숫자
    # "하의" 또는 "하의 :" 뒤에 30/32/34/36 숫자
    top_size_pattern = re.compile(
        r'상\s*의[^\n]*?\b(9[05]|10[05]|110|115)\b',
        re.DOTALL
    )
    bottom_size_pattern = re.compile(
        r'하\s*의[^\n]*?\b(2[8]|3[02468]|40)\b',
        re.DOTALL
    )
    has_top_size = bool(top_size_pattern.search(content))
    has_bottom_size = bool(bottom_size_pattern.search(content))
    if has_top_size and has_bottom_size:
        return True

    return False


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


# ═══════════════════════════════════════════════════════════════════
# 토큰 기반 상품명 추출 (Task 8)
# ═══════════════════════════════════════════════════════════════════
# 기존 라인기반 파싱(SALE 스킵 로직)은 SALE 위치·개수·포맷에 민감했음.
# 개선: <br>/줄바꿈을 공백으로 정규화 후 메타데이터를 전부 제거하고
# 남은 토큰을 상품명으로 사용 -> 포맷 변동에 강건.
# ═══════════════════════════════════════════════════════════════════

# 가격코드 패턴: 028 (QT) / 050 (AL) / 186 (QI) 등 - 3자리 숫자 + 알파벳 공장코드
_PRICE_CODE_FULL_RE = re.compile(r'\d{3}\s*\([A-Za-z][A-Za-z0-9]*\)')

# 브랜드 태그 (#FG, #LV 등)
_BRAND_TAG_ANY_RE = re.compile(r'#[A-Za-z]{2,4}\b')

# 실측 스펙 블록 - "어깨 44 45 46 48" / "가슴 50 52 54 57" / "허리 28 30" 등
# 키워드 + 공백 + 여러 숫자(소수점 허용)
_SIZE_SPEC_RE = re.compile(
    r'(어깨|가슴|허리|허벅지|소매|기장|밑위|총장|단면)\s*[:：\-~]?\s*[\d\.\s]+',
)

# SIZE SPEC 블록 이후 전부 제거 (영문 헤더)
_SIZE_SPEC_HEADER_RE = re.compile(r'\bSIZE\s*SPEC\b.*', re.IGNORECASE | re.DOTALL)

# 사이즈 포맷: "블랙 M L XL 2XL" / "화이트 95 100 105" / "남성 30 32 34 36"
# 색상명(한글 2자+) + 공백 + 사이즈 토큰 2개 이상
_COLOR_SIZE_LINE_RE = re.compile(
    r'\b[가-힣]{2,8}\s+(?:[MLXS24XL]+|\d{2,3})(?:\s+(?:[MLXS24XL]+|\d{2,3}))+\b'
)

# 사이즈/색상/소재 헤더 라인 (콜론/하이픈 뒤 내용까지 제거)
# 보수적 접근: 반드시 "키워드 - 값" 또는 "키워드 : 값" 포맷일 때만 매칭
# (콜론/하이픈 없으면 정상 상품명일 가능성이 높으므로 건드리지 않음)
_META_HEADER_RE = re.compile(
    r'(색\s*상|사이즈|SIZE|SIZES|소재|원단|성별|옵션)\s*[-:：]\s*[^\n#]*',
    re.IGNORECASE
)

# 옵션 라인 (상의 M ~ L / 하의 30 32 등 세트 옵션)
_SET_OPTION_LINE_RE = re.compile(
    r'\b(상의|하의)\s*[:：\-~]?\s*[^#\n]*'
)

# 소재/원단 설명 블록: "나일론 스판텍스 기능성 소재" / "면 50 폴리에스터 30 원단"
# (라인 끝이 소재/원단/혼용 으로 끝나면 앞쪽 섬유 재질 토큰 최대 5개까지 묶어서 제거)
# 주의: 상품 유형 키워드(반팔, 자켓 등)는 매칭 대상 제외
_FABRIC_WORDS = "나일론|폴리에스터|폴리에스테르|코튼|면|스판|스판덱스|스판텍스|레이온|울|리넨|캐시미어|실크|기능성|쿨링|메쉬|혼방"
_FABRIC_DESC_RE = re.compile(
    rf'(?:(?:{_FABRIC_WORDS})\s*\d*\s*%?\s*,?\s*){{1,6}}(소재|원단|혼용률|혼용)(?=\s|$)',
    re.IGNORECASE
)

# SALE / 세일 마커 (단어 경계)
_SALE_TOKEN_RE = re.compile(r'\b(SALE|sale|세일|Sale)\b')

# 이모지 (이후 통계에서 남은 이모지 제거용)
_EMOJI_RE = re.compile(
    r'[\U0001F000-\U0001FFFF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F'
    r'\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\u2600-\u27BF'
    r'\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF]+'
)

# 등급/홍보 태그 제거용 (민감정보 제거 로직에서도 사용하지만 여기서도 선제거)
_GRADE_PROMO_RE = re.compile(
    r'#?(?:SA급|고퀄|1:1|정품급|최상급|S급|A급|AA급|AAA급|신상|NEW|SALE)',
    re.IGNORECASE
)

# 수식 문자 (세트 상품 따옴표, 불릿 등)
_DECORATION_RE = re.compile(r'[🔘🔷🔆🍀✅💎⭐🌟▪️•※★☆♥♡❤️⚡]')

# 최종 결과에서 허용할 문자: 한글/영문/숫자/공백/기본 구두점
# 과도한 특수문자 정리용
_TRAILING_PUNCT_RE = re.compile(r'^[\s,\.\-_:;]+|[\s,\.\-_:;]+$')


def extract_product_name_from_tokens(raw_content: str) -> str:
    """
    토큰 기반 상품명 추출 (Task 8).
    
    <br>/줄바꿈을 공백으로 정규화 후 모든 메타데이터를 제거하고
    남은 텍스트를 상품명으로 반환한다. 라인 순서나 SALE 위치에 영향받지 않음.

    제거 대상:
    - <br> 태그, 줄바꿈, 탭
    - 브랜드 태그 (#FG, #LV 등)
    - 가격/공장코드 (028 (QT) 등)
    - SIZE SPEC 헤더 이후 전체
    - 실측치 블록 (어깨 44 45... / 가슴 50 52...)
    - 색상/사이즈 헤더 (색상 - / 사이즈 - / SIZE SPEC)
    - 색상 + 사이즈 조합 라인 (블랙 M L XL)
    - 세트 옵션 라인 (상의 95 100 / 하의 30 32)
    - SALE/세일 마커
    - 등급 태그 (#SA급 #고퀄)
    - 이모지 / 장식문자

    Args:
        raw_content: 밴드 게시글 원본 텍스트 (HTML 혹은 plain)
    
    Returns:
        정리된 상품명 문자열. 추출 실패시 빈 문자열.
    """
    if not raw_content:
        return ""
    
    text = raw_content
    
    # 1) HTML 태그 정규화 - <br> 먼저 공백으로, 나머지 태그는 제거
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'<band:hashtag>(.*?)</band:hashtag>', r' \1 ', text)
    text = re.sub(r'<band:refer[^>]*>(.*?)</band:refer>', r' \1 ', text)
    text = re.sub(r'<band:attachment[^/]*/>', ' ', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    
    # 2) SIZE SPEC 헤더 이후 전부 제거 (가장 먼저 - 이후 로직이 이 영역을 건드리지 않도록)
    text = _SIZE_SPEC_HEADER_RE.sub(' ', text)
    
    # 3) 줄바꿈/탭을 공백으로
    text = re.sub(r'[\n\r\t]+', ' ', text)
    
    # 4) 메타 토큰 제거 (순서 중요 - 긴 패턴 먼저)
    # 보수적 접근: 정상 상품명을 건드리지 않는 범위에서만 제거
    # 완전히 분류 실패한 건은 Task 9/10(엑셀+메일)에서 반자동 처리
    text = _PRICE_CODE_FULL_RE.sub(' ', text)       # 가격코드
    text = _GRADE_PROMO_RE.sub(' ', text)           # 등급 태그
    text = _FABRIC_DESC_RE.sub(' ', text)           # 소재/원단 설명 블록 (meta_header보다 먼저)
    text = _META_HEADER_RE.sub(' ', text)           # 색상/사이즈 헤더 (콜론/하이픈 있을 때만)
    text = _SIZE_SPEC_RE.sub(' ', text)             # 실측치 블록 (어깨 44 45...)
    text = _SET_OPTION_LINE_RE.sub(' ', text)       # 세트 옵션 (상의/하의 사이즈)
    text = _COLOR_SIZE_LINE_RE.sub(' ', text)       # 색상+사이즈 라인 (블랙 M L XL)
    text = _BRAND_TAG_ANY_RE.sub(' ', text)         # 브랜드 태그
    text = _SALE_TOKEN_RE.sub(' ', text)            # SALE 마커
    text = _EMOJI_RE.sub(' ', text)                 # 이모지
    text = _DECORATION_RE.sub(' ', text)            # 장식문자
    
    # 5) 민감정보 (연락처 / 소싱정보)
    text = re.sub(
        r'카카오톡|카톡|친구추가|e\d{4}|010[-\s]?\d{4}[-\s]?\d{4}',
        ' ', text
    )
    text = re.sub(
        r'국내배송[^₩]*₩[\d,]+|₩[\d,]+[^·]*국내배송|해외배송[^₩]*₩[\d,]+',
        ' ', text
    )

    # 6) 상품 부가정보 섹션 컷오프 (상품명 이후는 부가설명일 가능성 높음)
    # 이 마커 이후는 분류에 불필요한 부가 텍스트 (상품 구성/입고/사진참조 등)
    cutoff_markers = [
        r'\s*ㆍ\s*구성',
        r'\s*구성품\s*[-:ㅡ]',
        r'\s*-\s*구성',
        r'\s*-\s*color',
        r'\s*-\s*2color',
        r'\s*-\s*사이즈',
        r'\s*사진\s*동일',
        r'\s*주문시\s*코드',
        r'\s*={3,}',
        r'\s*재입고중',
        r'\s*입고\s*[ㆍ\s]',
        r'\s*품절',
        r'\s*신상\s*입고',
        r'\s*\*\s*color\s*[:：]',
        r'\s*\*\s*주문시',
    ]
    for pat in cutoff_markers:
        text = re.split(pat, text, maxsplit=1, flags=re.IGNORECASE)[0]

    # 7) 공백 정규화
    text = re.sub(r'\s+', ' ', text).strip()

    # 8) 앞뒤 구두점 정리
    text = _TRAILING_PUNCT_RE.sub('', text)

    return text.strip()


# 상품명 위치 판정 시 스킵할 마커 라인 (세트상품 따옴표 폴백 경로에서 사용)
_SALE_MARKER_RE = re.compile(r'^(sale|세일|SALE)$', re.IGNORECASE)


def _find_product_name_index(lines: list[str], brand_idx: int) -> int:
    """
    [Legacy] 브랜드 태그 이후 상품명이 위치한 라인 인덱스 반환.
    SALE/세일 마커 라인은 건너뛰고 실제 상품명을 가리킨다.
    
    NOTE: Task 8에서 토큰 기반 extract_product_name_from_tokens()로 교체됨.
    세트상품 파싱(parse_set_product)에서 따옴표 상품명이 없을 때의
    폴백 경로로만 유지한다.
    """
    idx = brand_idx + 1
    if idx >= len(lines):
        return idx
    if _SALE_MARKER_RE.match(lines[idx].strip()):
        next_idx = idx + 1
        if next_idx < len(lines):
            return next_idx
    return idx


def _is_price_line(line: str) -> bool:
    skip_prefixes = ['상의', '하의', '사이즈', '색상']
    stripped = line.strip()
    for prefix in skip_prefixes:
        if stripped.startswith(prefix):
            return False
    return True


# Task 9: 가격코드는 맨 마지막 줄 끝에 "숫자(알파벳)" 형태로 위치
# 사용자 규칙 기반 - 본문 중간의 사이즈 표기 "100 (M)" 오인식 방지
# 98.4% 일치율로 실데이터 검증됨 (기존 버그 케이스 자동 탐지)
_PRICE_CODE_AT_END_RE = re.compile(r'(\d{3})\s*\(([A-Za-z][A-Za-z0-9]*)\)\s*$')


def _extract_price(lines: list[str]) -> list[tuple[int, str, str | None]]:
    """
    가격코드 추출 - 맨 마지막 줄 끝 패턴 기반 (Task 9 수정).

    우선순위:
      1) 맨 마지막 라인 끝에 "숫자(문자)" 패턴 -> 채택
      2) 실패 시 마지막 3줄 중 가격 패턴 -> 채택 (사이즈별 가격 대응)

    본문 중간의 사이즈 표기 "100 (M)" 같은 건 가격으로 잡히지 않음.
    """
    if not lines:
        return []

    # 1차: 마지막 줄 끝
    last_line = lines[-1].strip()
    m = _PRICE_CODE_AT_END_RE.search(last_line)
    if m:
        price = int(m.group(1)) * 1000
        season = m.group(2)
        label = None
        if '상의' in last_line:
            label = 'top'
        elif '하의' in last_line:
            label = 'bottom'
        return [(price, season, label)]

    # 2차 폴백: 마지막 3줄 검색 (사이즈별 가격 케이스 등)
    results = []
    for line in lines[-3:]:
        stripped = line.strip()
        m = _PRICE_CODE_AT_END_RE.search(stripped)
        if m and _is_price_line(stripped):
            price = int(m.group(1)) * 1000
            season = m.group(2)
            label = None
            if '상의' in stripped:
                label = 'top'
            elif '하의' in stripped:
                label = 'bottom'
            results.append((price, season, label))
    return results


def _extract_price_set(lines: list[str]) -> list[tuple[int, str, str | None]]:
    """
    세트 상품 가격 추출 - 전체 줄 대상 (세트에는 상의/하의 가격이 따로 있을 수 있음).
    단 라인의 "끝"에 가격코드가 있는 경우만 매칭 (본문 중간 사이즈 표기 무시).
    """
    # 상의/하의 라벨 + 가격 패턴 (라인 끝)
    price_pattern = re.compile(
        r'^\s*(?:(상의|하의)\s+)?(\d{3})\s*\(([A-Za-z][A-Za-z0-9]*)\)\s*$'
    )
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

_PRICE_CODE_RE = re.compile(
    r'^\s*(?:상의|하의)?\s*\d{3}\s*\([A-Za-z][A-Za-z0-9]*\)\s*$'
)

_BRAND_TAG_RE = re.compile(r'^\s*#[A-Za-z]{2,4}\s*$')

_CONTACT_RE = re.compile(
    r'카카오톡|카톡|카톡채널|카카오.*채널|카카오.*문의|'
    r'카톡.*추가|카톡.*친구|친구추가|'
    r'톡문의|톡.*상담|채팅.*문의|'
    r'e\d{4}|'
    r'010[-\s]?\d{4}[-\s]?\d{4}',
    re.IGNORECASE
)

_GRADE_TAG_RE = re.compile(
    r'#(?:SA급|고퀄|1:1|정품급|최상급|S급|A급|AA급|AAA급)',
    re.IGNORECASE
)

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
        if _PRICE_CODE_RE.match(stripped):
            continue
        if _BRAND_TAG_RE.match(stripped):
            continue
        if _CONTACT_RE.search(stripped):
            continue

        cleaned_line = _GRADE_TAG_RE.sub('', stripped)
        cleaned_line = _SOURCING_INFO_RE.sub('', cleaned_line)
        cleaned_line = re.sub(r'#[A-Za-z]{2,4}\b', '', cleaned_line)
        cleaned_line = cleaned_line.strip()
        if cleaned_line:
            result.append(cleaned_line)

    return '\n'.join(result)


def parse_single_product(content: str, brand_map: dict) -> ParsedProduct:
    lines = [l.strip() for l in content.split('\n') if l.strip()]

    brand_tag, brand_name_en, brand_idx = _extract_brand(lines, brand_map)

    # Task 8: 토큰 기반 상품명 추출 (메타데이터 제거 후 남은 텍스트)
    # 라인 순서/SALE 위치에 영향받지 않는 robust 방식.
    product_name = extract_product_name_from_tokens(content)

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
        # Task 8: 따옴표 상품명 없을 때 토큰 기반 폴백
        product_name = extract_product_name_from_tokens(content)

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

    # 가격 2개 이상 -> 정상 세트 (상의/하의 가격 분리)
    if len(prices) >= 2:
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

    # Task 9: 가격이 1개만 있는 의류 세트 -> 단일 세트 상품으로 파싱
    # (가격 분리 불가능하므로 상/하의 분리 등록 X, WC에 1개 상품으로 등록)
    # 예: "#NK [N] 오서라이즈 세트 ... 048 (AL)"
    single_prices = _extract_price(lines)
    if single_prices:
        cost_price = single_prices[0][0]
        season_code = single_prices[0][1]
        # 사이즈는 상의 사이즈 우선 (있으면), 없으면 하의
        sizes = top_sizes if top_sizes else bottom_sizes
        # set_part="top" 지정 -> classify_category에서 set 카테고리로 분류됨
        # (WC에는 1개 상품으로 등록되지만 카테고리는 set)
        return [ParsedProduct(
            brand_tag=brand_tag,
            brand_name_en=brand_name_en,
            product_name=product_name,
            colors=colors,
            sizes=sizes,
            measurements=None,
            cost_price=cost_price,
            season_code=season_code,
            set_part="top",  # set 분류 트리거용
            source_band=""
        )]

    raise ParseError("세트 상품인데 가격코드가 없음")


def parse_post(content: str, brand_map: dict, source_band: str) -> list[ParsedProduct]:
    cleaned = preprocess_content(content)
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
