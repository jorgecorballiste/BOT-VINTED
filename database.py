"""SQLite persistence for seen listings."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Optional


@dataclass
class Listing:
    id: str
    title: str
    price: float
    currency: str
    condition: str
    url: str
    image_url: str
    seller: str
    published_at: Optional[str]
    search_term: str


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_listings (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL,
                    condition TEXT,
                    url TEXT NOT NULL,
                    image_url TEXT,
                    seller TEXT,
                    published_at TEXT,
                    search_term TEXT,
                    detected_at TEXT NOT NULL,
                    is_deal INTEGER NOT NULL DEFAULT 0,
                    ref_price REAL,
                    discount_pct REAL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_detected_at
                ON seen_listings(detected_at)
            """)

    def is_seen(self, listing_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_listings WHERE id = ?", (listing_id,)
            ).fetchone()
            return row is not None

    def mark_seen(
        self,
        listing: Listing,
        is_deal: bool = False,
        ref_price: Optional[float] = None,
        discount_pct: Optional[float] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_listings
                    (id, title, price, currency, condition, url, image_url,
                     seller, published_at, search_term, detected_at, is_deal,
                     ref_price, discount_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing.id,
                    listing.title,
                    listing.price,
                    listing.currency,
                    listing.condition,
                    listing.url,
                    listing.image_url,
                    listing.seller,
                    listing.published_at,
                    listing.search_term,
                    datetime.utcnow().isoformat(),
                    int(is_deal),
                    ref_price,
                    discount_pct,
                ),
            )

    def recent_deals(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT * FROM seen_listings
                WHERE is_deal = 1
                ORDER BY detected_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM seen_listings").fetchone()[0]
            deals = conn.execute(
                "SELECT COUNT(*) FROM seen_listings WHERE is_deal = 1"
            ).fetchone()[0]
            return {"total_seen": total, "total_deals": deals}
