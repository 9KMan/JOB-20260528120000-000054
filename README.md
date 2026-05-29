# Multi-Warehouse AI Agentic Inventory Controller

AI-powered stock balancing between Veeqo (WH1 + Amazon FBA) and Mintsoft (WH2) using CrewAI multi-agent orchestration.

## Stack
Python 3.11+ | CrewAI | PostgreSQL | Veeqo API | Mintsoft API | Slack HIL

## Quick Start
```bash
pip install -r requirements.txt
cp .env.example .env
psql $DATABASE_URL -f migrations/001_create_sku_map.sql
python scripts/run_sync.py
```

## Architecture
Veeqo API (WH1+FBA) → Unified SKU Mapping Table (PostgreSQL) ← Mintsoft API
                           ↓
                    CrewAI Balance Agent → Slack HIL [APPROVE][DENY]

## Key Files
- SPEC.md — Full technical specification
- migrations/001_create_sku_map.sql — Database schema
- src/agents/ — CrewAI agent definitions
- src/veeqo_client.py — Veeqo API wrapper
- src/mintsoft_client.py — Mintsoft API wrapper
- src/slack_hil_client.py — Slack Block Kit + button handler
- src/inventory_sync.py — Main sync cycle orchestrator

## Milestones
1. Foundation + API Connectors ($300)
2. AI Agent Layer + Transfer Logic ($400)
3. Slack HIL + Live POC ($300)
