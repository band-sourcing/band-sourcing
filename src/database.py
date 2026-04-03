import sqlite3
import json
from datetime import datetime


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_tag         TEXT NOT NULL,
                product_name      TEXT NOT NULL,
                set_part          TEXT DEFAULT NULL,
                cost_price        INTEGER NOT NULL,
                sell_price        INTEGER NOT NULL,
                margin_applied    INTEGER NOT NULL,
                wc_product_id     INTEGER NOT NULL,
                band_key          TEXT NOT NULL,
                post_key          TEXT NOT NULL,
                category          TEXT NOT NULL,
                created_at        DATETIME DEFAULT (datetime('now', 'localtime')),
                UNIQUE(brand_tag, product_name, set_part)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_tag         TEXT NOT NULL,
                product_name      TEXT NOT NULL,
                set_part          TEXT DEFAULT NULL,
                cost_price        INTEGER NOT NULL,
                post_key          TEXT NOT NULL,
                seen_at           DATETIME DEFAULT (datetime('now', 'localtime'))
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_price_lookup
                ON price_history(brand_tag, product_name, set_part)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_posts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                band_key          TEXT NOT NULL,
                post_key          TEXT NOT NULL UNIQUE,
                processed_at      DATETIME DEFAULT (datetime('now', 'localtime'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_logs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at        DATETIME NOT NULL,
                finished_at       DATETIME,
                posts_fetched     INTEGER DEFAULT 0,
                products_created  INTEGER DEFAULT 0,
                products_skipped  INTEGER DEFAULT 0,
                products_deleted  INTEGER DEFAULT 0,
                products_updated  INTEGER DEFAULT 0,
                errors            TEXT,
                status            TEXT DEFAULT 'running'
            )
        """)

        self.conn.commit()

    # ── 상품 CRUD ──

    def find_product(self, brand_tag: str, product_name: str, set_part: str = None) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM products WHERE brand_tag = ? AND product_name = ? AND set_part IS ?",
            (brand_tag, product_name, set_part)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def insert_product(self, **kwargs) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO products (brand_tag, product_name, set_part, cost_price,
                sell_price, margin_applied, wc_product_id, band_key, post_key, category)
            VALUES (:brand_tag, :product_name, :set_part, :cost_price,
                :sell_price, :margin_applied, :wc_product_id, :band_key, :post_key, :category)
        """, kwargs)
        self.conn.commit()
        return cursor.lastrowid

    def update_product_price(self, product_id: int, cost_price: int, sell_price: int, margin_applied: int):
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE products SET cost_price = ?, sell_price = ?, margin_applied = ?
            WHERE id = ?
        """, (cost_price, sell_price, margin_applied, product_id))
        self.conn.commit()

    def delete_product(self, product_id: int):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        self.conn.commit()

    def count_products(self) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products")
        return cursor.fetchone()[0]

    def get_oldest_products(self, limit: int) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM products ORDER BY created_at ASC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ── 가격 히스토리 ──

    def add_price_history(self, brand_tag: str, product_name: str,
                          set_part: str, cost_price: int, post_key: str):
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO price_history (brand_tag, product_name, set_part, cost_price, post_key)
            VALUES (?, ?, ?, ?, ?)
        """, (brand_tag, product_name, set_part, cost_price, post_key))
        self.conn.commit()

    def get_price_history(self, brand_tag: str, product_name: str,
                          set_part: str = None) -> list[int]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT cost_price FROM price_history WHERE brand_tag = ? AND product_name = ? AND set_part IS ?",
            (brand_tag, product_name, set_part)
        )
        return [row[0] for row in cursor.fetchall()]

    # ── 처리된 게시글 ──

    def is_post_processed(self, post_key: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM processed_posts WHERE post_key = ?", (post_key,)
        )
        return cursor.fetchone() is not None

    def mark_post_processed(self, band_key: str, post_key: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO processed_posts (band_key, post_key) VALUES (?, ?)",
            (band_key, post_key)
        )
        self.conn.commit()

    # ── 실행 로그 ──

    def start_run(self) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO run_logs (started_at, status) VALUES (?, 'running')",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
        )
        self.conn.commit()
        return cursor.lastrowid

    def finish_run(self, run_id: int, stats: dict, status: str):
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE run_logs SET
                finished_at = ?,
                posts_fetched = ?,
                products_created = ?,
                products_skipped = ?,
                products_deleted = ?,
                products_updated = ?,
                errors = ?,
                status = ?
            WHERE id = ?
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            stats.get("posts_fetched", 0),
            stats.get("products_created", 0),
            stats.get("products_skipped", 0),
            stats.get("products_deleted", 0),
            stats.get("products_updated", 0),
            json.dumps(stats.get("errors", []), ensure_ascii=False),
            status,
            run_id
        ))
        self.conn.commit()

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    db = Database("data/products.db")
    print("DB 생성 성공!")
    print(f"  상품 수: {db.count_products()}")
    print(f"  테이블: products, price_history, processed_posts, run_logs")
    db.close()
