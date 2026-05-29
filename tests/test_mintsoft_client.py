"""Tests for Mintsoft client."""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from datetime import datetime

import sys
sys.path.insert(0, '/home/deploy/squad/build-worker/JOB-20260528120000-000054/inventory-agent')

from src.mintsoft_client import MintsoftClient
from src.models import MintsoftInventoryItem


@pytest.fixture
def mintsoft_client():
    return MintsoftClient(api_key="test-key", base_url="https://api.test.mintsoft.co.uk")


@pytest.fixture
def mock_updated_since_response():
    return [
        {
            "product_id": 501,
            "sku": "MINT-SKU-001",
            "items": [
                {"sku": "MINT-SKU-001", "available": 8, "warehouse_id": 1, "warehouse_name": "Mintsoft WH1"}
            ]
        },
        {
            "product_id": 502,
            "sku": "MINT-SKU-002",
            "items": [
                {"sku": "MINT-SKU-002", "available": 60, "warehouse_id": 1, "warehouse_name": "Mintsoft WH1"}
            ]
        }
    ]


@pytest.mark.asyncio
async def test_fetch_updated_since_parses_items(mintsoft_client, mock_updated_since_response):
    """Test that fetch_updated_since correctly parses the response."""
    with patch.object(mintsoft_client, '_request_with_retry', new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_updated_since_response
        
        since = datetime(2024, 1, 1)
        result = await mintsoft_client.fetch_updated_since(since)
        
        assert len(result) == 2
        assert result[0]["product_id"] == 501
        assert result[0]["items"][0]["available"] == 8


@pytest.mark.asyncio
async def test_fetch_updated_since_with_no_timestamp(mintsoft_client, mock_updated_since_response):
    """Test that fetch_updated_since works without a timestamp (fetches all)."""
    with patch.object(mintsoft_client, '_request_with_retry', new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_updated_since_response
        
        result = await mintsoft_client.fetch_updated_since(None)
        
        assert len(result) == 2
        mock_req.assert_called_once()
