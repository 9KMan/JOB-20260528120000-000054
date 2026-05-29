"""Slack HIL (Human-in-the-Loop) client for transfer approvals."""

import os
import json
from typing import Tuple, Optional, Literal
import httpx
from .config import settings


class SlackHILClient:
    """Slack webhook client for sending HIL messages and parsing callbacks."""
    
    def __init__(self, webhook_url: Optional[str] = None, interactive_url: Optional[str] = None):
        self.webhook_url = webhook_url or settings.slack_webhook_url
        self.interactive_url = interactive_url or settings.slack_interactive_url
        self.channel = settings.slack_channel
    
    def send_transfer_proposal(
        self,
        master_sku: str,
        from_node: str,
        to_node: str,
        qty: int,
        reasoning: str,
        transfer_cost_estimate: float,
        profit_vs_cost: str
    ) -> bool:
        """Send a transfer proposal message to Slack with APPROVE/DENY buttons."""
        if not self.webhook_url:
            return False
        
        # Build Slack Block Kit message
        block_payload = {
            "channel": self.channel,
            "text": f":package: Transfer Proposal: {master_sku}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*📦 Transfer Proposal*\n*SKU:* `{master_sku}`\n"
                                f"*From:* {from_node} → *To:* {to_node}\n"
                                f"*Qty:* {qty}\n\n"
                                f"*Reasoning:* {reasoning}"
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Transfer Cost:*\n${transfer_cost_estimate:.2f}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Profit vs Cost:*\n:{profit_vs_cost.lower()}: {profit_vs_cost}"
                        }
                    ]
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ APPROVE"},
                            "style": "primary",
                            "value": f"{master_sku}|APPROVE|{qty}"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ DENY"},
                            "style": "danger",
                            "value": f"{master_sku}|DENY|{qty}"
                        }
                    ]
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Job: {settings.slack_channel} | Requires human approval before transfer execution"
                        }
                    ]
                }
            ]
        }
        
        try:
            response = httpx.post(self.webhook_url, json=block_payload, timeout=10.0)
            return response.status_code == 200
        except Exception:
            return False
    
    def send_promotion_recommendation(
        self,
        master_sku: str,
        reasoning: str,
        stock_node: str
    ) -> bool:
        """Send a promotion recommendation when transfer is not profitable."""
        if not self.webhook_url:
            return False
        
        block_payload = {
            "channel": self.channel,
            "text": f":tag: Promotion Recommendation: {master_sku}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*🏷️ Promotion Recommendation*\n*SKU:* `{master_sku}`\n"
                                f"*Node:* {stock_node}\n\n"
                                f"*Reasoning:* {reasoning}\n\n"
                                f"_Transfer cost exceeds margin profit. Consider localized promotion instead._"
                    }
                }
            ]
        }
        
        try:
            response = httpx.post(self.webhook_url, json=block_payload, timeout=10.0)
            return response.status_code == 200
        except Exception:
            return False
    
    def parse_callback(self, payload: dict) -> Tuple[Literal["APPROVE", "DENY"], str, int]:
        """Parse Slack interactive callback payload.
        
        Returns (action, master_sku, qty).
        """
        # payload comes from Slack's interactive message callback
        actions = payload.get("actions", [])
        if not actions:
            raise ValueError("No actions in callback payload")
        
        action_data = os.path.basename(actions[0].get("value", ""))
        parts = action_data.split("|")
        
        if len(parts) != 3:
            raise ValueError(f"Invalid action data format: {action_data}")
        
        master_sku = parts[0]
        action = parts[1].upper()
        qty = int(parts[2])
        
        if action not in ("APPROVE", "DENY"):
            raise ValueError(f"Invalid action: {action}")
        
        return (action, master_sku, qty)
    
    def send_decision_confirmation(
        self,
        master_sku: str,
        action: str,
        qty: int,
        success: bool,
        message: str = ""
    ) -> bool:
        """Send confirmation message after human decision."""
        if not self.webhook_url:
            return False
        
        status_emoji = "✅" if success else "❌"
        status_text = "APPROVED" if action == "APPROVE" else "DENIED"
        
        block_payload = {
            "channel": self.channel,
            "text": f"{status_emoji} Decision {status_text}: {master_sku}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{status_emoji} *Decision {status_text}*\n"
                                f"*SKU:* `{master_sku}`\n"
                                f"*Qty:* {qty}\n\n"
                                f"_{message}_"
                    }
                }
            ]
        }
        
        try:
            response = httpx.post(self.webhook_url, json=block_payload, timeout=10.0)
            return response.status_code == 200
        except Exception:
            return False


slack_hil_client = SlackHILClient()