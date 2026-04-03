#!/usr/bin/env python3
"""
DB 초기화 스크립트.

사용법:
  python scripts/init_db.py

data/products.db 생성 + 테이블 생성.
이미 DB가 존재하면 확인 메시지 출력 후 테이블만 보장.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import load_config
from src.database import Database


def main():
    config = load_config()
    db_path = config["database"]["path"]

    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    already_exists = os.path.exists(db_path)

    if already_exists:
        print(f"기존 DB 발견: {db_path}")
        print("테이블 구조를 확인합니다...")
    else:
        print(f"새 DB 생성: {db_path}")

    db = Database(db_path)

    tables = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [t[0] for t in tables]

    print(f"\n테이블 목록: {', '.join(table_names)}")
    print(f"상품 수: {db.count_products()}")

    db.close()

    if already_exists:
        print("\nDB 확인 완료.")
    else:
        print("\nDB 초기화 완료!")


if __name__ == "__main__":
    main()
