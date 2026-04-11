#!/bin/bash
# band-sourcing 패치 적용 스크립트
# 실행: bash /tmp/patch/apply_patch.sh

set -e

PROJECT="/opt/band-sourcing"
BACKUP="$PROJECT/backup_$(date +%Y%m%d_%H%M%S)"

echo "=== band-sourcing 패치 적용 ==="
echo "프로젝트: $PROJECT"
echo ""

# 1. 백업
echo "[1/5] 기존 파일 백업..."
mkdir -p "$BACKUP/src"
cp "$PROJECT/main.py" "$BACKUP/main.py" 2>/dev/null || true
cp "$PROJECT/src/config.py" "$BACKUP/src/config.py" 2>/dev/null || true
cp "$PROJECT/src/wc_uploader.py" "$BACKUP/src/wc_uploader.py" 2>/dev/null || true
cp "$PROJECT/src/band_scraper.py" "$BACKUP/src/band_scraper.py" 2>/dev/null || true
echo "  백업 완료: $BACKUP"

# 2. 파일 복사
echo "[2/5] 패치 파일 적용..."
PATCH_DIR="$(dirname "$0")"
cp "$PATCH_DIR/main.py" "$PROJECT/main.py"
cp "$PATCH_DIR/src/config.py" "$PROJECT/src/config.py"
cp "$PATCH_DIR/src/wc_uploader.py" "$PROJECT/src/wc_uploader.py"
cp "$PATCH_DIR/src/band_scraper.py" "$PROJECT/src/band_scraper.py"
echo "  4개 파일 적용 완료"

# 3. httpx 설치 (wc_uploader에서 사용)
echo "[3/5] httpx 의존성 확인..."
pip3 install httpx --quiet 2>/dev/null || pip install httpx --quiet 2>/dev/null
echo "  httpx 확인 완료"

# 4. 파싱 실패한 게시글 processed_posts에서 제거 (재시도 가능하도록)
echo "[4/5] 기존 파싱 실패 게시글 재시도 가능하도록 초기화..."
cd "$PROJECT"
python3 -c "
import sqlite3
conn = sqlite3.connect('data/products.db')
cursor = conn.cursor()

# processed_posts에서 products 테이블에 없는 post_key 삭제 (= 파싱 실패한 것들)
cursor.execute('''
    DELETE FROM processed_posts
    WHERE post_key NOT IN (SELECT DISTINCT post_key FROM products)
''')
deleted = cursor.rowcount
conn.commit()
conn.close()
print(f'  재시도 대상: {deleted}개 게시글 초기화 완료')
"

# 5. 검증
echo "[5/5] 패치 검증..."
cd "$PROJECT"
python3 -c "
from src.band_scraper import BandScraper
from src.config import load_config
from src.wc_uploader import WooCommerceUploader
print('  모듈 임포트 성공')

config = load_config()
print(f'  네이버 ID: {\"설정됨\" if config[\"band\"][\"naver_id\"] else \"미설정\"}')
print(f'  이미지없음 모드: {config[\"no_image_mode\"]}')
"

echo ""
echo "=== 패치 적용 완료 ==="
echo ""
echo "다음 단계:"
echo "  1. 테스트 실행: cd $PROJECT && xvfb-run python3 main.py"
echo "  2. 로그 확인: tail -50 $PROJECT/logs/\$(ls -t $PROJECT/logs/ | head -1)"
echo "  3. 이미지 확인 후 크론잡 등록"
echo ""
echo "이미지없음 모드 변경 (필요시):"
echo "  export NO_IMAGE_MODE=skip     # 이미지 없으면 등록 안 함"
echo "  export NO_IMAGE_MODE=register # 이미지 없어도 등록 (기본값)"
