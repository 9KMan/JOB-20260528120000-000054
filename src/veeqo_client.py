"""Veeqo API client with rate limiting and retry logic."""

import asyncio
import time
from typing import List, Dict, Any, Optional
import httpx
from .config import settings
from .models import VeeqoInventoryItem


class VeeqoRateLimitError(Exception):
    """Raised when Veeqo rate limit is exceeded."""
    pass


class VeeqoClient:
    """Veeqo API wrapper with rate limiting and exponential backoff retry."""
    
    BASE_URL = "https://api.veeqo.com"
    RATE_LIMIT = 100  # req/min
    RETRY_ATTEMPTS = 3
    BACKOFF_FACTOR = 2.0
    
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.veeqo_api_key
        self.base_url = base_url or settings.veeqo_base_url or self.BASE_URL
        self._request_count = 0
        self._minute_window_start = time.time()
        self._lock = asyncio.Lock()
    
    def _check_rate_limit(self):
        """Check and enforce rate limit."""
        now = time.time()
        elapsed = now - self._minute_window_start
        
        if elapsed >= 60:
            self._request_count = 0
            self._minute_window_start = now
        
        if self._request_count >= self.RATE_LIMIT:
            sleep_time = 60 - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            self._request_count = 0
            self._minute_window_start = time.time()
        
        self._request_count += 1
    
    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make HTTP request with exponential backoff retry."""
        headers = kwargs.pop("headers", {})
        headers["x-api-key"] = self.api_key
        
        last_exception = None
        for attempt in range(self.RETRY_ATTEMPTS):
            try:
                self._check_rate_limit()
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.request(
                        method,
                        f"{self.base_url}{endpoint}",
                        headers=headers,
                        **kwargs
                    )
                    
                    if response.status_code == 429:
                        raise VeeqoRateLimitError("Rate limit exceeded")
                    
                    response.raise_for_status()
                    return response.json()
                    
            except VeeqoRateLimitError:
                if attempt < self.RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(self.BACKOFF_FACTOR ** attempt * 2)
                    continue
                raise
            except httpx.HTTPStatusError as e:
                if attempt < self.RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(self.BACKOFF_FACTOR ** attempt)
                    continue
                raise
        
        raise last_exception
    
    async def fetch_inventory(self) -> List[VeeqoInventoryItem]:
        """Fetch all inventory items from Veeqo."""
        data = await self._request_with_retry("GET", "/inventory")
        
        items = []
        for warehouse_block in data if isinstance(data, list) else []:
            warehouse_name = warehouse_block.get("warehouse_name", "")
            is_fba = "amazon" in warehouse_name.lower()
            
            for product in warehouse_block.get("products", []):
                for variant in product.get("variants", []):
                    items.append(VeeqoInventoryItem(
                        product_id=str(product.get("product_id", "")),
                        variant_id=str(variant.get("variant_id", "")),
                        sku=variant.get("sku", ""),
                        available=variant.get("available", 0),
                        warehouse_name=warehouse_name,
                        is_fba=is_fba
                    ))
        
        return items
    
    async def fetch_fba_stock(self) -> List[VeeqoInventoryItem]:
        """Fetch Amazon FBA stock only."""
        data = await self._request_with_retry("GET", "/inventory")
        
        items = []
        for warehouse_block in data if isinstance(data, list) else []:
            warehouse_name = warehouse_block.get("warehouse_name", "")
            
            # Only process Amazon FBA warehouse
            if "amazon" not in warehouse_name.lower():
                continue
            
            for product in warehouse_block.get("products", []):
                for variant in product.get("variants", []):
                    items.append(VeeqoInventoryItem(
                        product_id=str(product.get("product_id", "")),
                        variant_id=str(variant.get("variant_id", "")),
                        sku=variant.get("sku", ""),
                        available=variant.get("available", 0),
                        warehouse_name=warehouse_name,
                        is_fba=True
                    ))
        
        return items
    
    async def update_stock(
        self,
        product_id: str,
        variant_id: str,
        quantity: int
    ) -> bool:
        """Update stock level for a product variant."""
        try:
            await self._request_with_retry(
                "PUT",
                f"/products/{product_id}/variants/{variant_id}",
                json={"available": quantity}
            )
            return True
        except Exception:
            return False
    
    async def bulk_stock_adjustment(self, adjustments: List[Dict[str, Any]]) -> bool:
        """Bulk stock adjustments via PUT /stock_entries."""
        try:
            await self._request_with_retry(
                "PUT",
                "/stock_entries",
                json={"adjustments": adjustments}
            )
            return True
        except Exception:
            return False


veeqo_client = VeeqoClient()