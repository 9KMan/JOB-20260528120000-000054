#!/usr/bin/env python3
"""CLI entrypoint for running the inventory sync cycle."""

import sys
import logging
from src.inventory_sync import run_sync_cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

if __name__ == "__main__":
    last_sync = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"Running sync cycle (last_sync={last_sync})...")
    result = run_sync_cycle(last_sync)
    print(f"Cycle complete: {result['imbalances_found']} imbalances found, {result['transfers_proposed']} proposed")