"""Main sync cycle orchestrator — coordinates the full inventory balance loop."""

import logging
from datetime import datetime, timezone
from .config import settings
from .db import DB
from .veeqo_client import VeeqoClient
from .mintsoft_client import MintsoftClient
from .slack_hil_client import SlackHILClient
from .agents.crew_setup import create_inventory_crew

logger = logging.getLogger(__name__)


def run_sync_cycle(last_sync_ts: str = None) -> dict:
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
    db = DB()

    # Step 1: Fetch Veeqo inventory (local + FBA)
    try:
        veeqo_data = veeqo.fetch_inventory()
        metrics["veeqo_fetched"] = len(veeqo_data)

        fba_data = veeqo.fetch_fba_stock()
        metrics["fba_fetched"] = len(fba_data)
        logger.info(f"Veeqo: {metrics['veeqo_fetched']} local SKUs, {metrics['fba_fetched']} FBA SKUs")
    except Exception as e:
        logger.error(f"Veeqo fetch error: {e}")
        metrics["errors"].append(f"Veeqo: {e}")

    # Step 2: Fetch Mintsoft inventory
    try:
        mintsoft_data = mintsoft.fetch_updated_since(last_sync_ts)
        metrics["mintsoft_fetched"] = len(mintsoft_data)
        logger.info(f"Mintsoft: {metrics['mintsoft_fetched']} SKUs updated")
    except Exception as e:
        logger.error(f"Mintsoft fetch error: {e}")
        metrics["errors"].append(f"Mintsoft: {e}")

    # Step 3: Update product_sku_map stock levels
    try:
        db.update_stock_levels(veeqo_data, fba_data, mintsoft_data)
        logger.info("SKU map stock levels updated")
    except Exception as e:
        logger.error(f"DB update error: {e}")
        metrics["errors"].append(f"DB update: {e}")

    # Step 4: Run CrewAI balance analysis
    try:
        crew = create_inventory_crew(veeqo, mintsoft, db, slack)
        # Kick off async crew run
        result = crew.kickoff()
        logger.info(f"CrewAI result: {result}")
    except Exception as e:
        logger.error(f"CrewAI error: {e}")
        metrics["errors"].append(f"CrewAI: {e}")

    # Step 5-7: Process decisions (Slack HIL handled by callback)
    # The crew returns TransferDecision objects — we send each to Slack HIL
    decisions = db.get_pending_decisions()
    metrics["imbalances_found"] = len(decisions)

    for decision in decisions:
        if decision["decision"] == "HEALTHY":
            logger.info(f"SKU {decision['master_sku']}: HEALTHY — no action")
        elif decision["decision"] in ("TRANSFER", "PROMOTE"):
            metrics["transfers_proposed"] += 1
            try:
                slack.send_transfer_proposal(
                    sku=decision["master_sku"],
                    from_node=decision.get("from_node"),
                    to_node=decision.get("to_node"),
                    qty=decision.get("qty", 0),
                    reasoning=decision.get("reasoning", ""),
                    transfer_cost=decision.get("transfer_cost_estimate", 0),
                    margin_profit=decision.get("margin_profit_on_stock", 0),
                    profit_vs_cost=decision.get("profit_vs_cost", "UNKNOWN")
                )
                logger.info(f"Slack HIL sent for {decision['master_sku']}")
            except Exception as e:
                logger.error(f"Slack HIL error: {e}")
                metrics["errors"].append(f"Slack: {e}")

    # Step 8: Log sync cycle
    try:
        db.log_sync_cycle(metrics)
    except Exception as e:
        logger.error(f"Sync log error: {e}")

    logger.info(f"Sync cycle complete: {metrics}")
    return metrics


def handle_slack_callback(payload: dict) -> dict:
    """
    Handle Slack button callback.
    Called when user clicks APPROVE or DENY on a transfer proposal.

    Payload structure:
        {"master_sku": "...", "action": "APPROVE|DENY", "qty": int}
    """
    sku = payload.get("master_sku")
    action = payload.get("action")
    qty = payload.get("qty", 0)

    if action == "APPROVE":
        # Execute the transfer via Mintsoft
        try:
            mintsoft = MintsoftClient(api_key=settings.mintsoft_api_key)
            decision = db.get_decision_for_sku(sku)
            result = mintsoft.create_transfer(
                from_warehouse=decision.get("from_node"),
                to_warehouse=decision.get("to_node"),
                sku=sku,
                qty=qty
            )
            db.mark_decision_approved(sku)
            return {"ok": True, "transfer_id": result.get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    else:
        # DENY — just log
        db.mark_decision_rejected(sku)
        return {"ok": True, "action": "DENY"}


if __name__ == "__main__":
    import sys
    last_sync = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_sync_cycle(last_sync)
    print(result)