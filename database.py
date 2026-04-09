"""SQLite persistence for seen listings."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    seller_id: str
    seller_rating: str
    location: str
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
                    seller_id TEXT,
                    seller_rating TEXT,
                    location TEXT,
                    published_at TEXT,
                    search_term TEXT,
                    detected_at TEXT NOT NULL,
                    is_deal INTEGER NOT NULL DEFAULT 0,
                    ref_price REAL,
                    discount_pct REAL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_detected_at "
                "ON seen_listings(detected_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_is_deal "
                "ON seen_listings(is_deal, detected_at)"
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add new columns to existing databases without breaking them."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(seen_listings)")}
        for col, col_type in [
            ("seller_id", "TEXT"),
            ("seller_rating", "TEXT"),
            ("location", "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE seen_listings ADD COLUMN {col} {col_type}")

    # ── Read operations ──────────────────────────────────────────────────────

    def is_seen(self, listing_id: str) -> bool:
        with self._conn() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM seen_listings WHERE id = ?", (listing_id,)
                ).fetchone()
                is not None
            )

    def bulk_are_seen(self, listing_ids: list[str]) -> set[str]:
        """Return the subset of IDs that are already stored — one query."""
        if not listing_ids:
            return set()
        placeholders = ",".join("?" * len(listing_ids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT id FROM seen_listings WHERE id IN ({placeholders})",
                listing_ids,
            ).fetchall()
        return {row["id"] for row in rows}

    def recent_deals(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM seen_listings "
                "WHERE is_deal = 1 ORDER BY detected_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def top_deals(self, limit: int = 5) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT title, price, ref_price, discount_pct, url, detected_at "
                "FROM seen_listings "
                "WHERE is_deal = 1 AND discount_pct IS NOT NULL "
                "ORDER BY discount_pct DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM seen_listings").fetchone()[0]
            deals = conn.execute(
                "SELECT COUNT(*) FROM seen_listings WHERE is_deal = 1"
            ).fetchone()[0]
            today_deals = conn.execute(
                "SELECT COUNT(*) FROM seen_listings "
                "WHERE is_deal = 1 AND detected_at >= date('now')"
            ).fetchone()[0]
        return {"total_seen": total, "total_deals": deals, "deals_today": today_deals}

    # ── Write operations ─────────────────────────────────────────────────────

    def mark_seen(
        self,
        listing: Listing,
        is_deal: bool = False,
        ref_price: Optional[float] = None,
        discount_pct: Optional[float] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_listings
                    (id, title, price, currency, condition, url, image_url,
                     seller, seller_id, seller_rating, location,
                     published_at, search_term, detected_at, is_deal,
                     ref_price, discount_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    listing.id, listing.title, listing.price, listing.currency,
                    listing.condition, listing.url, listing.image_url,
                    listing.seller, listing.seller_id, listing.seller_rating,
                    listing.location, listing.published_at, listing.search_term,
                    now, int(is_deal), ref_price, discount_pct,
                ),
            )

    def bulk_mark_seen(self, listings: list["Listing"], is_deal: bool = False) -> None:
        """Insert many non-deal listings in a single transaction."""
        if not listings:
            return
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                l.id, l.title, l.price, l.currency, l.condition, l.url,
                l.image_url, l.seller, l.seller_id, l.seller_rating, l.location,
                l.published_at, l.search_term, now, int(is_deal), None, None,
            )
            for l in listings
        ]
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO seen_listings
                    (id, title, price, currency, condition, url, image_url,
                     seller, seller_id, seller_rating, location,
                     published_at, search_term, detected_at, is_deal,
                     ref_price, discount_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )

    def cleanup_old(self, days: int = 30) -> int:
        """Delete non-deal records older than `days` days. Returns deleted count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM seen_listings WHERE is_deal = 0 AND detected_at < ?",
                (cutoff,),
            )
            return cur.rowcount
