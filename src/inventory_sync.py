"""Main sync cycle orchestrator — coordinates the full inventory balance loop."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from .config import settings
from .db import Database
from .veeqo_client import VeeqoClient
from .mintsoft_client import MintsoftClient
from .slack_hil_client import SlackHILClient
from .agents.crew_setup import create_inventory_crew, analyze_balance, calculate_transfer_cost

logger = logging.getLogger(__name__)


async def run_sync_cycle(last_sync_ts: Optional[str] = None) -> dict:
    """
    Execute one full inventory sync cycle.

    Steps:
    1. Fetch Veeqo + FBA inventory
    2. Fetch Mintsoft inventory (delta since last_sync_ts)
    3. Update product_sku_map with current stock levels
    4. Run CrewAI balance analysis
    5. For HEALTHY: log and skip
    6. For TRANSFER/PROMOTE: send Slack HIL message
    7. On APPROVE callback: execute Mintsoft transfer
    8. Log sync_cycle_log

    Returns dict with cycle metrics.
    """
    logger.info("Starting inventory sync cycle")
    metrics = {
        "veeqo_fetched": 0,
        "mintsoft_fetched": 0,
        "fba_fetched": 0,
        "imbalances_found": 0,
        "transfers_proposed": 0,
        "transfers_approved": 0,
        "transfers_executed": 0,
        "errors": []
    }

    # Init clients
    veeqo = VeeqoClient(api_key=settings.veeqo_api_key, base_url=settings.veeqo_base_url)
    mintsoft = MintsoftClient(api_key=settings.mintsoft_api_key, base_url=settings.mintsoft_base_url)
    slack = SlackHILClient(webhook_url=settings.slack_webhook_url)
    db = Database()
    await db.connect()

    # Step 1: Fetch Veeqo inventory (local + FBA)
    veeqo_data = []
    fba_data = []
    try:
        veeqo_data = await veeqo.fetch_inventory()
        metrics["veeqo_fetched"] = len(veeqo_data)

        fba_data = await veeqo.fetch_fba_stock()
        metrics["fba_fetched"] = len(fba_data)
        logger.info(f"Veeqo: {metrics['veeqo_fetched']} local SKUs, {metrics['fba_fetched']} FBA SKUs")
    except Exception as e:
        logger.error(f"Veeqo fetch error: {e}")
        metrics["errors"].append(f"Veeqo: {e}")

    # Step 2: Fetch Mintsoft inventory
    mintsoft_data = []
    try:
        since_dt = datetime.fromisoformat(last_sync_ts) if last_sync_ts else None
        mintsoft_data = await mintsoft.fetch_updated_since(since_dt)
        metrics["mintsoft_fetched"] = len(mintsoft_data)
        logger.info(f"Mintsoft: {metrics['mintsoft_fetched']} SKUs updated")
    except Exception as e:
        logger.error(f"Mintsoft fetch error: {e}")
        metrics["errors"].append(f"Mintsoft: {e}")

    # Step 3: Update product_sku_map stock levels using SKU mapping
    sku_map = {}  # master_sku -> {stock_veeqo_local, stock_fba, stock_mintsoft}
    try:
        # Aggregate Veeqo local stock (non-FBA items)
        for item in veeqo_data:
            if not item.is_fba:
                # Map veeqo_sku to master_sku
                mapped = await db.fetch_sku_map(item.sku, "veeqo")
                if mapped:
                    ms = mapped["master_sku"]
                    sku_map.setdefault(ms, {"stock_veeqo_local": 0, "stock_fba": 0, "stock_mintsoft": 0})
                    sku_map[ms]["stock_veeqo_local"] += item.available

        # Aggregate FBA stock
        for item in fba_data:
            mapped = await db.fetch_sku_map(item.sku, "veeqo")
            if mapped:
                ms = mapped["master_sku"]
                sku_map.setdefault(ms, {"stock_veeqo_local": 0, "stock_fba": 0, "stock_mintsoft": 0})
                sku_map[ms]["stock_fba"] += item.available

        # Aggregate Mintsoft stock
        for product in mintsoft_data:
            for item in product.get("items", []):
                mapped = await db.fetch_sku_map(item.get("sku", ""), "mintsoft")
                if mapped:
                    ms = mapped["master_sku"]
                    sku_map.setdefault(ms, {"stock_veeqo_local": 0, "stock_fba": 0, "stock_mintsoft": 0})
                    sku_map[ms]["stock_mintsoft"] += item.get("available", 0)

        # Write all aggregated stock levels to DB
        for master_sku, stocks in sku_map.items():
            await db.upsert_stock_levels(
                master_sku,
                stock_veeqo_local=stocks["stock_veeqo_local"],
                stock_fba=stocks["stock_fba"],
                stock_mintsoft=stocks["stock_mintsoft"]
            )
        logger.info(f"SKU map stock levels updated for {len(sku_map)} SKUs")
    except Exception as e:
        logger.error(f"DB update error: {e}")
        metrics["errors"].append(f"DB update: {e}")

    # Step 4: Run CrewAI balance analysis
    decisions = []
    try:
        crew = create_inventory_crew(veeqo, mintsoft, db, slack)
        result = await asyncio.to_thread(crew.kickoff)
        logger.info(f"CrewAI result: {result}")

        # Also run synchronous analysis for each SKU as fallback
        all_maps = await db.fetch_all_sku_maps()
        for sku_record in all_maps:
            ms = sku_record["master_sku"]
            stocks = sku_map.get(ms, {"stock_veeqo_local": 0, "stock_fba": 0, "stock_mintsoft": 0})
            decision = analyze_balance(
                veeqo_local=stocks["stock_veeqo_local"],
                fba_stock=stocks["stock_fba"],
                mintsoft_stock=stocks["stock_mintsoft"],
                min_veeqo=sku_record.get("min_safety_stock_veeqo", 10),
                min_fba=sku_record.get("min_safety_stock_fba", 15),
                min_mintsoft=sku_record.get("min_safety_stock_mintsoft", 10),
                excess_threshold=sku_record.get("excess_threshold", 50)
            )
            decision.master_sku = ms
            if decision.decision != "HEALTHY":
                decisions.append(decision)
    except Exception as e:
        logger.error(f"CrewAI error: {e}")
        metrics["errors"].append(f"CrewAI: {e}")

    # Step 5-7: Process decisions (Slack HIL handled by callback)
    metrics["imbalances_found"] = len(decisions)

    for decision in decisions:
        if decision.decision == "HEALTHY":
            logger.info(f"SKU {decision.master_sku}: HEALTHY — no action")
        elif decision.decision in ("TRANSFER", "PROMOTE"):
            metrics["transfers_proposed"] += 1
            try:
                if decision.decision == "TRANSFER":
                    slack.send_transfer_proposal(
                        master_sku=decision.master_sku,
                        from_node=decision.from_node,
                        to_node=decision.to_node,
                        qty=decision.qty,
                        reasoning=decision.reasoning,
                        transfer_cost_estimate=decision.transfer_cost_estimate,
                        profit_vs_cost=decision.profit_vs_cost
                    )
                else:
                    slack.send_promotion_recommendation(
                        master_sku=decision.master_sku,
                        reasoning=decision.reasoning,
                        stock_node=decision.from_node or "UNKNOWN"
                    )
                logger.info(f"Slack HIL sent for {decision.master_sku}")
            except Exception as e:
                logger.error(f"Slack HIL error: {e}")
                metrics["errors"].append(f"Slack: {e}")

    # Step 8: Log sync cycle
    try:
        await db.insert_sync_cycle_log(
            veeqo_fetched=metrics["veeqo_fetched"],
            mintsoft_fetched=metrics["mintsoft_fetched"],
            fba_fetched=metrics["fba_fetched"],
            imbalances_found=metrics["imbalances_found"],
            transfers_proposed=metrics["transfers_proposed"],
            transfers_approved=metrics["transfers_approved"],
            transfers_executed=metrics["transfers_executed"],
            errors="; ".join(metrics["errors"]) if metrics["errors"] else None
        )
    except Exception as e:
        logger.error(f"Sync log error: {e}")

    await db.disconnect()
    logger.info(f"Sync cycle complete: {metrics}")
    return metrics


async def handle_slack_callback(payload: dict) -> dict:
    """
    Handle Slack button callback.
    Called when user clicks APPROVE or DENY on a transfer proposal.

    Payload structure:
        {"master_sku": "...", "action": "APPROVE|DENY", "qty": int}
    """
    sku = payload.get("master_sku")
    action = payload.get("action")
    qty = payload.get("qty", 0)

    db = Database()
    await db.connect()

    try:
        if action == "APPROVE":
            # Fetch the SKU record to get warehouse IDs
            all_maps = await db.fetch_all_sku_maps()
            sku_record = next((r for r in all_maps if r["master_sku"] == sku), None)
            if not sku_record:
                return {"ok": False, "error": f"SKU {sku} not found in mapping table"}

            # Execute the transfer via Mintsoft
            mintsoft = MintsoftClient(api_key=settings.mintsoft_api_key)
            transfer_sku = sku_record.get("mintsoft_sku") or sku or ""
            result, transfer_id = await mintsoft.create_transfer(
                from_warehouse_id=sku_record.get("from_warehouse_id", 1),
                to_warehouse_id=sku_record.get("to_warehouse_id", 2),
                sku=transfer_sku,
                quantity=qty
            )
            if result:
                await db.update_transfer_log(sku or "", qty, "APPROVED")
                return {"ok": True, "transfer_id": transfer_id}
            return {"ok": False, "error": "Transfer creation failed"}
        else:
            # DENY — just log
            await db.update_transfer_log(sku or "", qty, "DENIED")
            return {"ok": True, "action": "DENY"}
    finally:
        await db.disconnect()


if __name__ == "__main__":
    import sys
    last_sync = sys.argv[1] if len(sys.argv) > 1 else None
    result = asyncio.run(run_sync_cycle(last_sync))
    print(result)