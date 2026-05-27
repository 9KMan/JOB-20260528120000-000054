"""Database connection and queries using asyncpg."""

from typing import Optional, List, Dict, Any
from datetime import datetime
import asyncpg
from .config import settings


class Database:
    """Async PostgreSQL database handler."""

    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Create connection pool."""
        self.pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10
        )

    async def disconnect(self):
        """Close connection pool."""
        if self.pool:
            await self.pool.close()

    async def fetch_sku_map(self, external_sku: str, source: str) -> Optional[Dict[str, Any]]:
        """Look up master_sku by external SKU (veeqo or mintsoft)."""
        if not self.pool:
            await self.connect()

        column = "veeqo_sku" if source == "veeqo" else "mintsoft_sku"
        query = f"""
            SELECT * FROM product_sku_map WHERE {column} = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, external_sku)
            return dict(row) if row else None

    async def fetch_all_sku_maps(self) -> List[Dict[str, Any]]:
        """Fetch all SKU mapping records."""
        if not self.pool:
            await self.connect()

        query = "SELECT * FROM product_sku_map"
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
            return [dict(row) for row in rows]

    async def upsert_stock_levels(
        self,
        master_sku: str,
        stock_veeqo_local: Optional[int] = None,
        stock_fba: Optional[int] = None,
        stock_mintsoft: Optional[int] = None
    ):
        """Update stock levels for a SKU."""
        if not self.pool:
            await self.connect()

        query = """
            UPDATE product_sku_map
            SET stock_veeqo_local = COALESCE($2, stock_veeqo_local),
                stock_fba = COALESCE($3, stock_fba),
                stock_mintsoft = COALESCE($4, stock_mintsoft),
                updated_at = CURRENT_TIMESTAMP
            WHERE master_sku = $1
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, master_sku, stock_veeqo_local, stock_fba, stock_mintsoft)

    async def insert_sync_cycle_log(
        self,
        veeqo_fetched: int = 0,
        mintsoft_fetched: int = 0,
        fba_fetched: int = 0,
        imbalances_found: int = 0,
        transfers_proposed: int = 0,
        transfers_approved: int = 0,
        transfers_executed: int = 0,
        errors: Optional[str] = None
    ) -> int:
        """Insert sync cycle log and return the inserted ID."""
        if not self.pool:
            await self.connect()

        query = """
            INSERT INTO sync_cycle_log
            (cycle_at, veeqo_fetched, mintsoft_fetched, fba_fetched,
             imbalances_found, transfers_proposed, transfers_approved,
             transfers_executed, errors)
            VALUES (CURRENT_TIMESTAMP, $1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query, veeqo_fetched, mintsoft_fetched, fba_fetched,
                imbalances_found, transfers_proposed, transfers_approved,
                transfers_executed, errors
            )
            return row["id"]

    async def update_transfer_log(
        self,
        master_sku: str,
        qty: int,
        direction: str
    ):
        """Update last transfer info for a SKU."""
        if not self.pool:
            await self.connect()

        query = """
            UPDATE product_sku_map
            SET last_transfer_at = CURRENT_TIMESTAMP,
                last_transfer_qty = $2,
                last_transfer_direction = $3
            WHERE master_sku = $1
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, master_sku, qty, direction)


db = Database()