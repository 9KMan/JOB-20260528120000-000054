"""Mintsoft API client with rate limiting and retry logic."""

import asyncio
import time
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import httpx
from .config import settings
from .models import MintsoftInventoryItem


class MintsoftClient:
    """Mintsoft API wrapper with rate limiting and exponential backoff retry."""
    
    BASE_URL = "https://api.mintsoft.co.uk"
    RATE_LIMIT = 200  # req/min
    RETRY_ATTEMPTS = 3
    BACKOFF_FACTOR = 2.0
    
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.mintsoft_api_key
        self.base_url = base_url or settings.mintsoft_base_url or self.BASE_URL
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
        headers["APIKey"] = self.api_key
        
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
                    
                    response.raise_for_status()
                    return response.json()
                    
            except httpx.HTTPStatusError as e:
                if attempt < self.RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(self.BACKOFF_FACTOR ** attempt)
                    continue
                raise
            
            except Exception as e:
                last_exception = e
                if attempt < self.RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(self.BACKOFF_FACTOR ** attempt)
                    continue
                raise
        
        raise last_exception
    
    async def fetch_inventory(self, product_id: int) -> List[MintsoftInventoryItem]:
        """Fetch inventory for a specific product."""
        data = await self._request_with_retry(
            "GET",
            f"/api/Product/{product_id}/Inventory/PreOrderBreakdown/All"
        )
        
        items = []
        if isinstance(data, dict):
            for warehouse in data.get("warehouses", []):
                for item in warehouse.get("items", []):
                    items.append(MintsoftInventoryItem(
                        product_id=product_id,
                        sku=item.get("sku", ""),
                        available=item.get("available", 0),
                        warehouse_id=warehouse.get("warehouse_id", 0),
                        warehouse_name=warehouse.get("warehouse_name", "Default")
                    ))
        
        return items
    
    async def fetch_updated_since(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Fetch all products updated since timestamp (delta sync)."""
        if since is None:
            since = datetime.min
        
        params = {"since": since.isoformat()}
        data = await self._request_with_retry(
            "GET",
            "/api/Product/UpdatedSince",
            params=params
        )
        
        if isinstance(data, list):
            return data
        return data.get("products", []) if isinstance(data, dict) else []
    
    async def create_transfer(
        self,
        from_warehouse_id: int,
        to_warehouse_id: int,
        sku: str,
        quantity: int
    ) -> Tuple[bool, Optional[str]]:
        """Create inter-warehouse transfer.
        
        Returns (success, transfer_id).
        """
        try:
            payload = {
                "from_warehouse_id": from_warehouse_id,
                "to_warehouse_id": to_warehouse_id,
                "sku": sku,
                "quantity": quantity
            }
            result = await self._request_with_retry(
                "POST",
                "/api/WarehouseTransfer/Create",
                json=payload
            )
            
            transfer_id = result.get("transfer_id") or result.get("id")
            return True, str(transfer_id) if transfer_id else None
            
        except Exception as e:
            return False, str(e)


mintsoft_client = MintsoftClient()