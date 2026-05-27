"""Pydantic models for inventory domain."""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


class SKUMap(BaseModel):
    """SKU mapping record from product_sku_map table."""
    id: Optional[int] = None
    master_sku: str
    product_name: Optional[str] = None
    veeqo_sku: Optional[str] = None
    veeqo_product_id: Optional[str] = None
    veeqo_variant_id: Optional[str] = None
    mintsoft_sku: Optional[str] = None
    mintsoft_product_id: Optional[int] = None
    min_safety_stock_veeqo: int = 10
    min_safety_stock_mintsoft: int = 10
    min_safety_stock_fba: int = 15
    excess_threshold: int = 50
    stock_veeqo_local: int = 0
    stock_fba: int = 0
    stock_mintsoft: int = 0
    last_transfer_at: Optional[datetime] = None
    last_transfer_qty: Optional[int] = None
    last_transfer_direction: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class StockLevel(BaseModel):
    """Represents stock level at a specific node."""
    node: Literal["VEEQO_LOCAL", "VEEQO_FBA", "MINTSOFT"]
    quantity: int
    min_safety_stock: int
    is_low: bool = False
    is_excess: bool = False

    def __init__(self, **data):
        super().__init__(**data)
        if self.quantity < self.min_safety_stock:
            self.is_low = True
        if self.quantity > self.min_safety_stock + data.get("excess_threshold", 50):
            self.is_excess = True


class TransferDecision(BaseModel):
    """AI agent decision output."""
    decision: Literal["TRANSFER", "PROMOTE", "HEALTHY"]
    master_sku: str
    from_node: Optional[Literal["VEEQO_LOCAL", "VEEQO_FBA", "MINTSOFT"]] = None
    to_node: Optional[Literal["VEEQO_LOCAL", "VEEQO_FBA", "MINTSOFT"]] = None
    qty: int = 0
    reasoning: str = ""
    transfer_cost_estimate: float = 0.0
    margin_profit_on_stock: float = 0.0
    profit_vs_cost: Literal["PROFITABLE", "LOSSPROPOSAL"] = "LOSSPROPOSAL"


class SyncCycleLog(BaseModel):
    """Sync cycle log entry."""
    id: Optional[int] = None
    cycle_at: Optional[datetime] = None
    veeqo_fetched: int = 0
    mintsoft_fetched: int = 0
    fba_fetched: int = 0
    imbalances_found: int = 0
    transfers_proposed: int = 0
    transfers_approved: int = 0
    transfers_executed: int = 0
    errors: Optional[str] = None


class VeeqoInventoryItem(BaseModel):
    """Veeqo inventory item structure."""
    product_id: str
    variant_id: str
    sku: str
    available: int
    warehouse_name: str
    is_fba: bool = False


class MintsoftInventoryItem(BaseModel):
    """Mintsoft inventory item structure."""
    product_id: int
    sku: str
    available: int
    warehouse_id: int
    warehouse_name: str = "Default"