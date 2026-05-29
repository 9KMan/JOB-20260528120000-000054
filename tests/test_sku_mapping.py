"""Tests for SKU mapping and balance analysis."""
import pytest
import sys
sys.path.insert(0, '/home/deploy/squad/build-worker/JOB-20260528120000-000054/inventory-agent')

from src.agents.crew_setup import analyze_balance, calculate_transfer_cost
from src.models import TransferDecision


def test_analyze_balance_veeqo_excess_mintsoft_low():
    """Rule 1: Veeqo local excess + Mintsoft low → transfer Veeqo to Mintsoft."""
    decision = analyze_balance(
        veeqo_local=100,   # excess (min 10 + threshold 50 = 60, so 100 is excess)
        fba_stock=20,     # healthy
        mintsoft_stock=5,  # low (below min 10)
        min_veeqo=10,
        min_fba=15,
        min_mintsoft=10,
        excess_threshold=50
    )
    
    assert decision.decision == "TRANSFER"
    assert decision.from_node == "VEEQO_LOCAL"
    assert decision.to_node == "MINTSOFT"
    assert decision.qty > 0


def test_analyze_balance_fba_low_veeqo_excess():
    """Rule 2: FBA low + Veeqo excess → transfer to FBA."""
    decision = analyze_balance(
        veeqo_local=100,   # excess
        fba_stock=5,       # low (below min 15)
        mintsoft_stock=20,  # healthy
        min_veeqo=10,
        min_fba=15,
        min_mintsoft=10,
        excess_threshold=50
    )
    
    assert decision.decision == "TRANSFER"
    assert decision.to_node == "VEEQO_FBA"
    assert decision.qty > 0


def test_analyze_balance_healthy():
    """All nodes within thresholds → HEALTHY."""
    decision = analyze_balance(
        veeqo_local=50,
        fba_stock=30,
        mintsoft_stock=40,
        min_veeqo=10,
        min_fba=15,
        min_mintsoft=10,
        excess_threshold=50
    )
    
    assert decision.decision == "HEALTHY"


def test_analyze_balance_never_below_safety():
    """Transfer should never drop stock below safety threshold."""
    decision = analyze_balance(
        veeqo_local=55,    # barely excess
        fba_stock=5,      # low
        mintsoft_stock=30,
        min_veeqo=10,
        min_fba=15,
        min_mintsoft=10,
        excess_threshold=50
    )
    
    # qty should be limited so we don't drop Veeqo below min
    assert decision.qty <= 45  # 55 - 10 min safety


def test_calculate_transfer_cost_base():
    """Base shipping cost for non-FBA transfer."""
    cost = calculate_transfer_cost("VEEQO_LOCAL", "MINTSOFT", 10)
    assert cost == 2.50


def test_calculate_transfer_cost_fba_inbound():
    """FBA transfers include inbound fees."""
    cost = calculate_transfer_cost("VEEQO_LOCAL", "VEEQO_FBA", 10)
    assert cost == 2.50 + (3.00 * 10)  # base + inbound fee per unit
