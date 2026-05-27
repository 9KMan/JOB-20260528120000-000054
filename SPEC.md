# SPEC: Multi-Warehouse AI Agentic Inventory Controller

## 1. Concept & Vision

An autonomous AI-powered inventory orchestration layer that bridges two structurally distinct warehouse management systems — **Veeqo** (WH1, with Amazon FBA) and **Mintsoft** (WH2) — using a **Unified SKU Mapping Table** as the translation layer. The agent continuously monitors stock imbalances across all nodes (Veeqo Local, Mintsoft, Amazon FBA), reasons about transfer profitability, and routes actionable recommendations through a **Human-in-the-Loop Slack approval workflow**. Built as production-grade Python services with CrewAI/LangChain at the core.

---

## 2. Architecture Overview

```
┌─────────────────────┐    ┌─────────────────────┐
│  Veeqo API (WH1)    │    │  Mintsoft API (WH2) │
│  + Amazon FBA levels│    │                     │
└──────────┬──────────┘    └──────────┬──────────┘
           │                          │
           ▼                          ▼
┌──────────────────────────────────────────────────┐
│         Unified SKU Mapping Table (PostgreSQL)   │
│  master_sku | veeqo_sku | mintsoft_sku | FBA qty │
└───────────────────────────┬──────────────────────┘
                            │
           ┌────────────────┴────────────────┐
           ▼                                 ▼
┌─────────────────────┐            ┌─────────────────────┐
│  AI Stock Balancing  │            │   Slack HIL         │
│  Agent (CrewAI)      │───────────▶│   Webhook          │
│  - Evaluate balance   │            │   [APPROVE][DENY]  │
│  - Profit check        │            └─────────────────────┘
│  - Draft transfer      │
└─────────────────────┘
```

**Flow:** Cron trigger → Fetch Veeqo + Mintsoft inventory → Map SKUs → AI agent reasoning → Decision: healthy (log) or transfer draft (Slack HIL) → Team approves/denies via Slack buttons → On approval: POST Mintsoft warehouse transfer.

---

## 3. Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| AI Framework | CrewAI (multi-agent) or LangChain (agentic) |
| LLM | gpt-4o or claude-3-5-sonnet (configurable) |
| Database | PostgreSQL 15+ |
| Veeqo Wrapper | REST API + x-api-key auth |
| Mintsoft Wrapper | REST API + APIKey auth |
| Slack Integration | Slack Incoming Webhooks + Interactive Buttons |
| Hosting | AWS Lambda / ECS (configurable) |
| Scheduler | APScheduler (cron-based) or Event-Driven Webhook |
| Env Management | python-dotenv, pydantic Settings |

---

## 4. Data Model — Unified SKU Mapping Table

```sql
CREATE TABLE product_sku_map (
    id SERIAL PRIMARY KEY,

    -- Internal master SKU (source of truth)
    master_sku VARCHAR(255) UNIQUE NOT NULL,
    product_name VARCHAR(255),

    -- Warehouse 1: Veeqo identifiers
    veeqo_sku VARCHAR(255) UNIQUE NOT NULL,
    veeqo_product_id VARCHAR(100),
    veeqo_variant_id VARCHAR(100),

    -- Warehouse 2: Mintsoft identifiers
    mintsoft_sku VARCHAR(255) UNIQUE NOT NULL,
    mintsoft_product_id INT,

    -- Operational thresholds
    min_safety_stock_veeqo INT DEFAULT 10,
    min_safety_stock_mintsoft INT DEFAULT 10,
    min_safety_stock_fba INT DEFAULT 15,
    excess_threshold INT DEFAULT 50,

    -- Real-time stock (updated each sync cycle)
    stock_veeqo_local INT DEFAULT 0,
    stock_fba INT DEFAULT 0,
    stock_mintsoft INT DEFAULT 0,

    -- Transfer log
    last_transfer_at TIMESTAMP,
    last_transfer_qty INT,
    last_transfer_direction VARCHAR(20),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_veeqo_sku ON product_sku_map(veeqo_sku);
CREATE INDEX idx_mintsoft_sku ON product_sku_map(mintsoft_sku);
CREATE INDEX idx_master_sku ON product_sku_map(master_sku);
```

### Sync State Table (per-cycle tracking)
```sql
CREATE TABLE sync_cycle_log (
    id SERIAL PRIMARY KEY,
    cycle_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    veeqo_fetched INT,
    mintsoft_fetched INT,
    fba_fetched INT,
    imbalances_found INT,
    transfers_proposed INT,
    transfers_approved INT,
    transfers_executed INT,
    errors TEXT
);
```

---

## 5. API Integration Specifications

### A. Veeqo API
- **Base URL:** `https://api.veeqo.com`
- **Auth:** `x-api-key: {API_KEY}` header
- **Endpoints:**
  - `GET /inventory` — fetch all inventory with warehouse splits and FBA levels
  - `PUT /products/{product_id}/variants/{variant_id}` — update stock levels
  - `PUT /stock_entries` — bulk stock adjustments

> **FBA parsing:** Veeqo returns Amazon FBA stock as a separate warehouse location. Parser must isolate the warehouse block where `marketplace == "amazon"` and extract `available` quantity.

### B. Mintsoft API
- **Base URL:** `https://api.mintsoft.co.uk`
- **Auth:** `APIKey: {API_KEY}` header (or OAuth2 for Swagger)
- **Endpoints:**
  - `GET /api/Product/{id}/Inventory/PreOrderBreakdown/All` — per-product inventory
  - `GET /api/Product/UpdatedSince?since={timestamp}` — delta inventory since last sync
  - `POST /api/WarehouseTransfer/Create` — create inter-warehouse transfer for approval

### C. Slack HIL Webhook
- **Webhook URL:** Stored in `SLACK_WEBHOOK_URL` env var
- **Message format:** Block Kit with:
  - Section: SKU summary, current stock at each node, imbalance ratio
  - Actions: `button[APPROVE]` / `button[DENY]` with `value: "{master_sku}|{action}|{transfer_qty"`
- **Interactive URL:** `SLACK_INTERACTIVE_URL` for button callbacks (Slack workspace apps)

---

## 6. AI Agent Design — Stock Balancing Controller

### Agent Role Definition
```
You are the Multi-Warehouse Inventory Balance Controller. Your job is to monitor
stock levels across Veeqo (local warehouse + Amazon FBA) and Mintsoft, detect
imbalances, and decide whether to propose a stock transfer.

THRESHOLDS:
- min_safety_stock_veeqo: default 10
- min_safety_stock_mintsoft: default 10
- min_safety_stock_fba: default 15
- excess_threshold: stock > 50 above minimum = candidate for transfer

REASONING RULES:
1. If Veeqo local stock is EXCESS and Mintsoft is LOW → propose transfer Mintsoft → Veeqo
2. If FBA stock is LOW and either Veeqo local or Mintsoft has EXCESS → propose transfer to FBA
3. If Mintsoft is EXCESS and Veeqo (local or FBA) is LOW → propose transfer Veeqo → Mintsoft
4. Before proposing: calculate transfer cost (shipping + FBA inbound fees). If transfer
   cost exceeds the margin profit on the slow stock, DECLINE transfer and instead recommend
   a localized promotion via Slack.
5. Never propose a transfer that would drop any node below its min_safety_stock.

OUTPUT FORMAT (JSON):
{
  "decision": "TRANSFER" | "PROMOTE" | "HEALTHY",
  "master_sku": "...",
  "from_node": "VEEQO_LOCAL" | "VEEQO_FBA" | "MINTSOFT",
  "to_node": "VEEQO_LOCAL" | "VEEQO_FBA" | "MINTSOFT",
  "qty": integer,
  "reasoning": "...",
  "transfer_cost_estimate": float,
  "margin_profit_on_stock": float,
  "profit_vs_cost": "PROFITABLE" | "LOSSPROPOSAL"
}
```

### Multi-Agent CrewAI Structure (preferred over LangChain)
```
Crew: Inventory Balance Crew

Agent 1: Inventory Fetcher
  Role: Fetch current stock levels from Veeqo and Mintsoft
  Tools: VeeqoClient, MintsoftClient

Agent 2: SKU Mapping Engine
  Role: Normalize all external SKUs to master_sku using product_sku_map table
  Tools: PostgresDB

Agent 3: Balance Analyzer (Manager/Controller)
  Role: Evaluate imbalances per reasoning rules above
  Tools: Reasoning engine, threshold config

Agent 4: Transfer Planner
  Role: Construct transfer payloads and Slack messages
  Tools: SlackWebhookClient, MintsoftTransferAPI
```

---

## 7. Core Services

### `inventory_sync.py` — Main sync cycle
```python
def run_sync_cycle():
    # 1. Fetch Veeqo + FBA inventory
    veeqo_data = veeqo_client.fetch_all_inventory()
    fba_data = veeqo_client.fetch_fba_stock()

    # 2. Fetch Mintsoft inventory
    mintsoft_data = mintsoft_client.fetch_updated_since(last_sync_ts)

    # 3. Update product_sku_map with current stock levels

    # 4. Run CrewAI multi-agent analysis

    # 5. For each imbalance:
    #    - If HEALTHY: log and skip
    #    - If TRANSFER/PROMOTE: send Slack HIL message
    #    - If APPROVED (callback): execute Mintsoft transfer

    # 6. Log sync_cycle_log
```

### `veeqo_client.py`
- Rate limit: 100 req/min (backoff on 429)
- Retry: 3 attempts with exponential backoff
- Methods: `fetch_inventory()`, `fetch_fba_stock()`, `update_stock(product_id, variant_id, qty)`

### `mintsoft_client.py`
- Rate limit: 200 req/min
- Retry: 3 attempts
- Methods: `fetch_inventory(product_id)`, `fetch_updated_since(ts)`, `create_transfer(from_wh, to_wh, sku, qty)`

### `slack_hil_client.py`
- `send_transfer_proposal(sku, from_node, to_node, qty, reasoning)`
- `parse_callback(payload)` → returns `(master_sku, action, qty)`

---

## 8. Configuration (.env)

```env
# AI / LLM
OPENAI_API_KEY=sk-...
# or
ANTHROPIC_API_KEY=sk-ant-...

# Database
DATABASE_URL=postgresql://user:pass@host:5432/inventory_db

# Veeqo
VEEQO_API_KEY=vxk_...
VEEQO_BASE_URL=https://api.veeqo.com

# Mintsoft
MINTSOFT_API_KEY=ms_...
MINTSOFT_BASE_URL=https://api.mintsoft.co.uk

# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
SLACK_INTERACTIVE_URL=https://hooks.slack.com/services/...  # for button callbacks
SLACK_CHANNEL=#inventory-ops

# Agent Config
LLM_MODEL=gpt-4o
SYNC_INTERVAL_MINUTES=15
TRANSFER_FEE_OVERRIDE=0.0  # manual override for transfer cost calculation
```

---

## 9. File Structure

```
inventory-agent/
├── SPEC.md
├── README.md
├── .env.example
├── requirements.txt
├── pyproject.toml
├── migrations/
│   └── 001_create_sku_map.sql
├── src/
│   ├── __init__.py
│   ├── config.py              # pydantic Settings from env
│   ├── db.py                  # asyncpg connection + queries
│   ├── veeqo_client.py        # Veeqo API wrapper
│   ├── mintsoft_client.py      # Mintsoft API wrapper
│   ├── slack_hil_client.py     # Slack webhook + button handler
│   ├── models.py              # Pydantic models (SKU, StockLevel, Transfer)
│   ├── inventory_sync.py      # Main sync cycle orchestrator
│   └── agents/
│       ├── __init__.py
│       ├── crew_setup.py       # CrewAI crew definition
│       └── prompts.py          # Agent system prompts
├── tests/
│   ├── test_veeqo_client.py
│   ├── test_mintsoft_client.py
│   ├── test_sku_mapping.py
│   ├── test_balance_agent.py
│   └── mock_data/
│       ├── veeqo_inventory.json
│       ├── mintsoft_inventory.json
│       └── fba_stock.json
└── scripts/
    └── run_sync.py             # CLI entrypoint
```

---

## 10. Milestones

### Milestone 1: Foundation + API Connectors ($300)
- PostgreSQL schema migration (product_sku_map + sync_cycle_log)
- Veeqo client: fetch inventory + FBA stock with rate limiting/retry
- Mintsoft client: fetch inventory + UpdatedSince delta
- SKU mapping engine: normalize external SKUs → master_sku
- `.env.example` + config.py

### Milestone 2: AI Agent Layer + Transfer Logic ($400)
- CrewAI multi-agent crew (Fetcher → Mapper → Analyzer → Planner)
- Transfer cost calculation (shipping + FBA inbound fees vs margin profit)
- Decision logic: TRANSFER / PROMOTE / HEALTHY
- Full reasoning trace logged per cycle

### Milestone 3: Slack HIL + POC ($300)
- Slack Block Kit messages with APPROVE/DENY buttons
- Button callback handler: parse approval → execute Mintsoft transfer
- Rejection handling: log reason, no action
- Live POC: trigger imbalance → Slack message → approve → transfer confirmed

---

## 11. Acceptance Criteria

- [ ] `migrations/001_create_sku_map.sql` creates both tables with indexes
- [ ] `veeqo_client.py` fetches both local warehouse stock AND Amazon FBA stock separately
- [ ] `mintsoft_client.py` fetches inventory and supports UpdatedSince delta
- [ ] SKU mapping correctly resolves Veeqo SKU ≠ Mintsoft SKU → same master_sku
- [ ] Mock test: given mismatched SKUs in mock data, system normalizes and logs correct balance decision
- [ ] AI agent correctly declines transfer when cost > margin profit
- [ ] Slack message contains: SKU, stock at each node (Veeqo Local, FBA, Mintsoft), qty to transfer, cost estimate
- [ ] Slack APPROVE button triggers real Mintsoft transfer creation via API
- [ ] Sync cycle log written with all metrics per run
- [ ] README: Quick Start (3 steps: env, migrate, run) + Architecture diagram (ASCII) + data sources table