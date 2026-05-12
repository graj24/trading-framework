"""Main entry point for the Autonomous Trading Framework."""
from __future__ import annotations

import argparse
import yaml
from dotenv import load_dotenv

from core.logger import setup_logging
from agents.master import MasterAgent


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    load_dotenv()
    config = load_config()
    setup_logging(config)

    import logging
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="Autonomous Trading Framework")
    parser.add_argument("--schedule", action="store_true", help="Start 24/7 scheduler")
    parser.add_argument("--once", action="store_true", help="Run scheduler jobs once and exit")
    args = parser.parse_args()

    if args.schedule:
        from core.scheduler import start
        logger.info("Starting 24/7 scheduler...")
        start()
        return

    if args.once:
        from core.scheduler import run_once
        run_once()
        return

    # Default: single analysis cycle
    logger.info("=" * 60)
    logger.info("Autonomous Trading Framework")
    logger.info(f"Mode: {config['trading']['mode'].upper()} | Capital: ₹{config['trading']['capital']:,}")
    logger.info(f"Watchlist: {', '.join(config['watchlist'])}")
    logger.info("=" * 60)

    master = MasterAgent(config)
    for symbol in config["watchlist"]:
        result = master.run_for_stock(symbol)
        if result.ok():
            d = result.data
            logger.info(f"{symbol}: {d['decision']} (conf={d['confidence']}%) — {d['reasoning']}")


if __name__ == "__main__":
    main()
