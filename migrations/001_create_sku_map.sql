-- Migration: Create SKU Mapping and Sync Cycle Log tables
-- Multi-Warehouse AI Agentic Inventory Controller

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

-- Sync State Table (per-cycle tracking)
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