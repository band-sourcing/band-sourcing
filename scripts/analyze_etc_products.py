#!/usr/bin/env python3
"""
[Step 4 분석용] etc/accessory 분류 상품 상품명 추출 + 패턴 분석.

서버에서 실행:
  cd /opt/band-sourcing
  source venv/bin/activate
  python3 scripts/analyze_etc_products.py
"""

import sqlite3
import os
import sys
import re
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main():
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    )
    if not os.path.exists(db_path):
        print(f"DB 파일 없음: {db_path}")
        return

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    # 1) 전체 카테고리별 분포
    print("=" * 60)
    print("1. 현재 DB 카테고리별 분포")
    print("=" * 60)
    cur = db.cursor()
    cur.execute("SELECT category, COUNT(*) as cnt FROM products GROUP BY category ORDER BY cnt DESC")
    for row in cur.fetchall():
        print(f"  {row['category']}: {row['cnt']}건")

    # 2) etc + accessory 상품명 전체 추출
    print("\n" + "=" * 60)
    print("2. etc / accessory 분류 상품명 전체")
    print("=" * 60)
    cur.execute(
        "SELECT brand_tag, product_name, category, cost_price "
        "FROM products WHERE category IN ('etc', 'accessory') "
        "ORDER BY category, product_name"
    )
    rows = cur.fetchall()
    print(f"  총 {len(rows)}건\n")

    # 상품명 단어 빈도 분석
    word_counter = Counter()
    for row in rows:
        name = row["product_name"]
        # 한글 단어 추출 (2글자 이상)
        words = re.findall(r'[가-힣]{2,}', name)
        word_counter.update(words)
        # 영문 단어 추출 (2글자 이상)
        en_words = re.findall(r'[a-zA-Z]{2,}', name)
        word_counter.update([w.upper() for w in en_words])

    # 3) etc/accessory 상품명 출력 (전체)
    for row in rows:
        print(f"  [{row['category']}] {row['brand_tag']} | {row['product_name']} | {row['cost_price']:,}원")

    # 4) 상위 빈출 단어 (잠재 키워드 후보)
    print("\n" + "=" * 60)
    print("3. etc/accessory 상품명 빈출 단어 TOP 50")
    print("=" * 60)
    # 이미 키워드에 포함된 단어 제외
    known_keywords = {
        "가방", "백", "토트", "숄더백", "크로스백", "클러치", "핸드백", "배낭", "백팩",
        "시계", "워치", "자켓", "블루종", "패딩", "코트", "바람막이", "점퍼", "아우터",
        "티셔츠", "긴팔", "반팔", "맨투맨", "후디", "후드", "니트", "스웨터", "셔츠",
        "팬츠", "바지", "슬랙스", "데님", "청바지", "진", "조거", "트레이닝",
        "벨트", "머플러", "스카프", "선글라스", "안경", "모자", "캡", "비니",
        "지갑", "카드홀더", "월렛", "스니커즈", "운동화", "로퍼", "구두", "부츠",
        "슬리퍼", "샌들", "신발", "뮬", "슈즈", "폴로", "블라우스",
        "가디건", "집업", "짚업", "베스트", "반바지", "쇼츠", "숏팬츠",
        "치마", "스커트", "레깅스", "카고", "크루넥", "탱크탑", "나시", "원피스",
    }
    for word, cnt in word_counter.most_common(80):
        marker = "" if word in known_keywords else " ◀ NEW"
        if cnt >= 2:
            print(f"  {word}: {cnt}회{marker}")

    # 5) 성별 분류 현황
    print("\n" + "=" * 60)
    print("4. 의류 카테고리 성별 분포 (DB 기준)")
    print("=" * 60)
    # DB에는 성별 컬럼이 없으므로 WC 카테고리 ID로 역추적 불가
    # 대신 상품명에 여성 키워드가 있는 상품 수 확인
    cur.execute("SELECT product_name FROM products")
    all_products = cur.fetchall()
    female_kw_products = []
    for row in all_products:
        name = row["product_name"].upper()
        if any(kw in name for kw in ["WOMEN", "우먼", "여성", "WOMAN", "레이디"]):
            female_kw_products.append(row["product_name"])

    print(f"  상품명에 여성 키워드 포함: {len(female_kw_products)}건")
    for name in female_kw_products[:30]:
        print(f"    - {name}")
    if len(female_kw_products) > 30:
        print(f"    ... 외 {len(female_kw_products) - 30}건")

    db.close()
    print("\n분석 완료!")


if __name__ == "__main__":
    main()
