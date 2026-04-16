#!/usr/bin/env python3
"""
마이그레이션: products + price_history 테이블 변경
  1. set_part: NULL → '' (빈 문자열) 정규화
  2. products UNIQUE 제약 변경:
     BEFORE: UNIQUE(brand_tag, product_name, set_part)
     AFTER:  UNIQUE(brand_tag, product_name, set_part, cost_price)

SQLite는 ALTER TABLE로 UNIQUE 변경 불가 → 테이블 재생성 방식.
실행 전 자동 백업 생성.
"""
import os
import sys
import shutil
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src.config import load_config


def migrate(db_path: str):
    # ── 1. 백업 ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[1/6] 백업 완료: {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # ── 2. 기존 데이터 카운트 ──
        cursor.execute("SELECT COUNT(*) FROM products")
        prod_before = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM price_history")
        ph_before = cursor.fetchone()[0]
        print(f"[2/6] 기존 데이터: products={prod_before}개  price_history={ph_before}개")

        # ── 3. price_history의 NULL set_part → '' ──
        cursor.execute("UPDATE price_history SET set_part = '' WHERE set_part IS NULL")
        ph_updated = cursor.rowcount
        print(f"[3/6] price_history NULL→'' 변환: {ph_updated}건")

        # ── 4. products 새 테이블 생성 ──
        cursor.execute("""
            CREATE TABLE products_new (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_tag         TEXT NOT NULL,
                product_name      TEXT NOT NULL,
                set_part          TEXT NOT NULL DEFAULT '',
                cost_price        INTEGER NOT NULL,
                sell_price        INTEGER NOT NULL,
                margin_applied    INTEGER NOT NULL,
                wc_product_id     INTEGER NOT NULL,
                band_key          TEXT NOT NULL,
                post_key          TEXT NOT NULL,
                category          TEXT NOT NULL,
                created_at        DATETIME DEFAULT (datetime('now', 'localtime')),
                UNIQUE(brand_tag, product_name, set_part, cost_price)
            )
        """)
        print("[4/6] products_new 테이블 생성 완료")

        # ── 5. 데이터 복사 (NULL set_part → '') ──
        cursor.execute("""
            INSERT INTO products_new
                (id, brand_tag, product_name, set_part, cost_price,
                 sell_price, margin_applied, wc_product_id, band_key,
                 post_key, category, created_at)
            SELECT
                id, brand_tag, product_name,
                COALESCE(set_part, ''),
                cost_price, sell_price, margin_applied, wc_product_id,
                band_key, post_key, category, created_at
            FROM products
        """)
        print("[5/6] 데이터 복사 완료 (NULL→'' 변환 포함)")

        # ── 6. 테이블 교체 ──
        cursor.execute("DROP TABLE products")
        cursor.execute("ALTER TABLE products_new RENAME TO products")
        conn.commit()

        # 검증
        cursor.execute("SELECT COUNT(*) FROM products")
        prod_after = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM price_history")
        ph_after = cursor.fetchone()[0]
        print(f"[6/6] 마이그레이션 완료!")
        print(f"   products: {prod_before} → {prod_after}개")
        print(f"   price_history: {ph_before} → {ph_after}개")

        if prod_before != prod_after:
            print(f"⚠️  경고: products 데이터 수 불일치! 백업에서 복구하세요: {backup_path}")
            sys.exit(1)

        # NULL 잔존 체크
        cursor.execute("SELECT COUNT(*) FROM products WHERE set_part IS NULL")
        null_count = cursor.fetchone()[0]
        if null_count > 0:
            print(f"⚠️  경고: products에 NULL set_part가 {null_count}건 남아있음!")
        else:
            print("   ✓ NULL set_part 없음 확인")

        # UNIQUE 제약 확인
        cursor.execute("PRAGMA index_list(products)")
        indexes = cursor.fetchall()
        for idx in indexes:
            cursor.execute(f"PRAGMA index_info({idx[1]})")
            idx_cols = [row[2] for row in cursor.fetchall()]
            print(f"   인덱스 [{idx[1]}]: {idx_cols}")

        print(f"\n✅ 성공! 백업 파일: {backup_path}")
        print(f"   문제 발생 시 복구: cp {backup_path} {db_path}")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ 마이그레이션 실패: {e}")
        print(f"   백업에서 복구: cp {backup_path} {db_path}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    config = load_config()
    db_path = config["database"]["path"]

    if not os.path.exists(db_path):
        print(f"DB 파일 없음: {db_path}")
        print("새로 시작하는 경우 마이그레이션 불필요 (database.py가 새 스키마로 생성)")
        sys.exit(0)

    print("=" * 50)
    print("밴드 소싱 DB 마이그레이션")
    print("  1) set_part: NULL → '' (빈 문자열)")
    print("  2) UNIQUE(brand_tag, product_name, set_part)")
    print("     → UNIQUE(brand_tag, product_name, set_part, cost_price)")
    print("=" * 50)
    print()

    confirm = input("진행하시겠습니까? (y/N): ").strip().lower()
    if confirm != "y":
        print("취소됨")
        sys.exit(0)

    migrate(db_path)
