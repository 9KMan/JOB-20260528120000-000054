"""CrewAI crew definition for inventory balancing."""

from typing import List, Dict, Any
from crewai import Agent, Crew, Task, Process
from langchain_openai import ChatOpenAI
from .prompts import (
    BALANCE_AGENT_SYSTEM_PROMPT,
    INVENTORY_FETCHER_PROMPT,
    SKU_MAPPER_PROMPT,
    TRANSFER_PLANNER_PROMPT
)
from ..config import settings
from ..models import TransferDecision


def create_inventory_crew(
    veeqo_client,
    mintsoft_client,
    db,
    slack_client
) -> Crew:
    """Create and configure the CrewAI crew for inventory balancing."""
    
    # Initialize LLM based on provider
    if settings.llm_provider == "openai":
        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            temperature=0.3
        )
    else:
        # Anthropic via LangChain
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(
            model=settings.llm_model,
            api_key=settings.anthropic_api_key,
            temperature=0.3
        )
    
    # Agent 1: Inventory Fetcher
    fetcher_agent = Agent(
        role="Inventory Fetcher",
        goal="Fetch accurate stock levels from all warehouse systems",
        backstory=INVENTORY_FETCHER_PROMPT,
        llm=llm,
        verbose=True
    )
    
    # Agent 2: SKU Mapping Engine
    mapper_agent = Agent(
        role="SKU Mapping Engine",
        goal="Normalize all external SKUs to master_sku",
        backstory=SKU_MAPPER_PROMPT,
        llm=llm,
        verbose=True
    )
    
    # Agent 3: Balance Analyzer
    analyzer_agent = Agent(
        role="Balance Analyzer",
        goal="Evaluate imbalances and make transfer decisions",
        backstory=BALANCE_AGENT_SYSTEM_PROMPT,
        llm=llm,
        verbose=True
    )
    
    # Agent 4: Transfer Planner
    planner_agent = Agent(
        role="Transfer Planner",
        goal="Construct transfer payloads and Slack messages",
        backstory=TRANSFER_PLANNER_PROMPT,
        llm=llm,
        verbose=True
    )
    
    # Tasks
    fetch_task = Task(
        description="Fetch inventory from Veeqo and Mintsoft",
        agent=fetcher_agent,
        expected_output="List of all SKU stock levels"
    )
    
    map_task = Task(
        description="Map all external SKUs to master_sku",
        agent=mapper_agent,
        expected_output="Dict mapping master_sku to stock levels"
    )
    
    analyze_task = Task(
        description="Analyze imbalances and produce transfer decisions",
        agent=analyzer_agent,
        expected_output="List of TransferDecision objects"
    )
    
    plan_task = Task(
        description="Plan transfers and Slack messages",
        agent=planner_agent,
        expected_output="List of Slack HIL messages ready to send"
    )
    
    # Crew with sequential process
    crew = Crew(
        agents=[fetcher_agent, mapper_agent, analyzer_agent, planner_agent],
        tasks=[fetch_task, map_task, analyze_task, plan_task],
        process=Process.sequential,
        verbose=True
    )
    
    return crew


def calculate_transfer_cost(from_node: str, to_node: str, qty: int) -> float:
    """Calculate estimated transfer cost.
    
    Includes shipping + FBA inbound fees where applicable.
    Uses TRANSFER_FEE_OVERRIDE from settings if set.
    """
    if settings.transfer_fee_override > 0:
        return settings.transfer_fee_override
    
    # Base cost factors
    base_shipping = 2.50
    fba_inbound_fee = 3.00  # per unit for FBA inbound
    
    cost = base_shipping
    
    if to_node == "VEEQO_FBA" or from_node == "VEEQO_FBA":
        cost += fba_inbound_fee * qty
    
    return cost


def calculate_margin_profit(sku_data: Dict[str, Any]) -> float:
    """Estimate margin profit on stock.
    
    This is a simplified calculation. In production, this would
    come from actual product margins in the system.
    """
    # Default margin assumption - would be from product data in production
    default_unit_margin = 15.0
    return default_unit_margin


def analyze_balance(
    veeqo_local: int,
    fba_stock: int,
    mintsoft_stock: int,
    min_veeqo: int = 10,
    min_fba: int = 15,
    min_mintsoft: int = 10,
    excess_threshold: int = 50
) -> TransferDecision:
    """Analyze current stock levels and produce a transfer decision.
    
    This is a synchronous version used when not running full CrewAI.
    """
    # Check for excess and low conditions
    veeqo_excess = veeqo_local > min_veeqo + excess_threshold
    veeqo_low = veeqo_local < min_veeqo
    fba_low = fba_stock < min_fba
    fba_excess = fba_stock > min_fba + excess_threshold
    mintsoft_excess = mintsoft_stock > min_mintsoft + excess_threshold
    mintsoft_low = mintsoft_stock < min_mintsoft
    
    # Rule 1: If Veeqo local is EXCESS and Mintsoft is LOW → propose transfer Mintsoft → Veeqo
    if veeqo_excess and mintsoft_low:
        qty = min(veeqo_local - min_veeqo, mintsoft_stock - min_mintsoft)
        if qty > 0:
            return TransferDecision(
                decision="TRANSFER",
                master_sku="",  # Will be filled by caller
                from_node="VEEQO_LOCAL",
                to_node="MINTSOFT",
                qty=qty,
                reasoning="Veeqo excess, Mintsoft low",
                transfer_cost_estimate=calculate_transfer_cost("VEEQO_LOCAL", "MINTSOFT", qty),
                profit_vs_cost="PROFITABLE"
            )
    
    # Rule 2: If FBA stock is LOW and either Veeqo local or Mintsoft has EXCESS → propose transfer to FBA
    if fba_low:
        if veeqo_excess:
            qty = min(veeqo_local - min_veeqo, 50)
            if qty > 0:
                return TransferDecision(
                    decision="TRANSFER",
                    master_sku="",
                    from_node="VEEQO_LOCAL",
                    to_node="VEEQO_FBA",
                    qty=qty,
                    reasoning="FBA low, Veeqo local excess",
                    transfer_cost_estimate=calculate_transfer_cost("VEEQO_LOCAL", "VEEQO_FBA", qty),
                    profit_vs_cost="PROFITABLE"
                )
        if mintsoft_excess:
            qty = min(mintsoft_stock - min_mintsoft, 50)
            if qty > 0:
                return TransferDecision(
                    decision="TRANSFER",
                    master_sku="",
                    from_node="MINTSOFT",
                    to_node="VEEQO_FBA",
                    qty=qty,
                    reasoning="FBA low, Mintsoft excess",
                    transfer_cost_estimate=calculate_transfer_cost("MINTSOFT", "VEEQO_FBA", qty),
                    profit_vs_cost="PROFITABLE"
                )
    
    # Rule 3: If Mintsoft is EXCESS and Veeqo (local or FBA) is LOW → propose transfer Veeqo → Mintsoft
    if mintsoft_excess and (veeqo_low or fba_low):
        source = "VEEQO_FBA" if fba_low else "VEEQO_LOCAL"
        source_stock = fba_stock if fba_low else veeqo_local
        qty = min(mintsoft_stock - min_mintsoft, source_stock - (min_fba if fba_low else min_veeqo))
        if qty > 0:
            return TransferDecision(
                decision="TRANSFER",
                master_sku="",
                from_node=source,
                to_node="MINTSOFT",
                qty=qty,
                reasoning="Mintsoft excess, Veeqo low",
                transfer_cost_estimate=calculate_transfer_cost(source, "MINTSOFT", qty),
                profit_vs_cost="PROFITABLE"
            )
    
    return TransferDecision(
        decision="HEALTHY",
        master_sku="",
        reasoning="All stock levels within healthy thresholds"
    )
