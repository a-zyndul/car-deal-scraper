import sqlite3
from contextlib import closing

from adapters.base import Listing


class Database:
    """SQLite: deduplikacja ogłoszeń (klucz = platforma:id)."""

    def __init__(self, db_path: str = "cars.db"):
        self.db_path = db_path
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_cars (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    title TEXT,
                    price TEXT,
                    url TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def is_seen(self, uid: str) -> bool:
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute("SELECT 1 FROM seen_cars WHERE id = ?", (uid,)).fetchone() is not None

    def add(self, l: Listing) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_cars (id, platform, title, price, url) VALUES (?,?,?,?,?)",
                (l.uid, l.platform, l.title, str(l.price) if l.price is not None else None, l.url),
            )
            conn.commit()

    def count(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM seen_cars").fetchone()[0]
