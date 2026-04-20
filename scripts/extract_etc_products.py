#!/usr/bin/env python3
"""
미분류(etc) 상품 전체 추출 스크립트.

서버에서 실행:
  cd /opt/band-sourcing
  source venv/bin/activate
  python3 scripts/extract_etc_products.py

출력:
  1) 터미널에 전체 리스트 출력
  2) data/etc_products.csv 로 CSV 저장
"""

import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main():
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "products.db"
    )
    if not os.path.exists(db_path):
        print(f"DB 파일 없음: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # etc 카테고리 상품 전체 추출
    cursor = conn.execute(
        """
        SELECT id, brand_tag, product_name, cost_price, category, wc_product_id
        FROM products
        WHERE category = 'etc'
        ORDER BY id
        """
    )
    rows = cursor.fetchall()

    if not rows:
        print("etc 카테고리 상품이 없습니다")
        conn.close()
        return

    # 터미널 출력
    print("=" * 100)
    print(f"미분류(etc) 상품 전체 리스트 — 총 {len(rows)}건")
    print("=" * 100)
    print(
        f"{'ID':>5} | {'브랜드':^15} | {'상품명':<45} | {'원가':>8} | {'WC_ID':>7}"
    )
    print("-" * 100)
    for row in rows:
        print(
            f"{row['id']:>5} | {row['brand_tag']:^15} | "
            f"{row['product_name'][:45]:<45} | "
            f"{row['cost_price']:>7,} | {row['wc_product_id']:>7}"
        )

    # CSV 저장
    csv_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "etc_products.csv"
    )
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["id", "brand_tag", "product_name", "cost_price", "category", "wc_product_id"]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["brand_tag"],
                    row["product_name"],
                    row["cost_price"],
                    row["category"],
                    row["wc_product_id"],
                ]
            )

    print(f"\n총 {len(rows)}건 추출 완료")
    print(f"CSV 저장: {csv_path}")

    conn.close()


if __name__ == "__main__":
    main()
