"""Tests for Veeqo client."""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

# Path for mocking
import sys
sys.path.insert(0, '/home/deploy/squad/build-worker/JOB-20260528120000-000054/inventory-agent')

from src.veeqo_client import VeeqoClient, VeeqoRateLimitError
from src.models import VeeqoInventoryItem


@pytest.fixture
def veeqo_client():
    return VeeqoClient(api_key="test-key", base_url="https://api.test.veeqo.com")


@pytest.fixture
def mock_inventory_response():
    return [
        {
            "warehouse_name": "Main Warehouse",
            "products": [
                {
                    "product_id": "101",
                    "variants": [
                        {"variant_id": "201", "sku": "VEEQO-SKU-001", "available": 75},
                        {"variant_id": "202", "sku": "VEEQO-SKU-002", "available": 5}
                    ]
                }
            ]
        },
        {
            "warehouse_name": "Amazon EU",
            "products": [
                {
                    "product_id": "101",
                    "variants": [
                        {"variant_id": "201", "sku": "VEEQO-SKU-001", "available": 30}
                    ]
                }
            ]
        }
    ]


@pytest.mark.asyncio
async def test_fetch_inventory_parses_local_and_fba(veeqo_client, mock_inventory_response):
    """Test that fetch_inventory correctly separates local warehouse from FBA."""
    with patch.object(veeqo_client, '_request_with_retry', new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_inventory_response
        
        items = await veeqo_client.fetch_inventory()
        
        # Should have 3 items total: 2 local (Main Warehouse) + 1 FBA (Amazon EU)
        assert len(items) == 3
        
        local_items = [i for i in items if not i.is_fba]
        fba_items = [i for i in items if i.is_fba]
        
        assert len(local_items) == 2
        assert len(fba_items) == 1
        assert fba_items[0].warehouse_name == "Amazon EU"


@pytest.mark.asyncio
async def test_fetch_fba_stock_returns_only_fba(veeqo_client, mock_inventory_response):
    """Test that fetch_fba_stock returns only Amazon FBA items."""
    with patch.object(veeqo_client, '_request_with_retry', new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_inventory_response
        
        items = await veeqo_client.fetch_fba_stock()
        
        assert len(items) == 1
        assert items[0].is_fba is True
        assert items[0].warehouse_name == "Amazon EU"
        assert items[0].available == 30


def test_rate_limit_enforcement(veeqo_client):
    """Test that rate limit is enforced after RATE_LIMIT requests."""
    import time
    veeqo_client._request_count = 0
    veeqo_client._minute_window_start = time.time()
    veeqo_client.RATE_LIMIT = 10
    
    for i in range(10):
        veeqo_client._check_rate_limit()
    
    # Next call should trigger sleep
    start = time.time()
    veeqo_client._check_rate_limit()
    elapsed = time.time() - start
    
    assert elapsed >= 0.1  # Should have slept
