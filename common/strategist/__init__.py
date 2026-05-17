"""PM Strategist — entrypoint.

Usage:
    python -m common.strategist --pm_id 1
    python -m common.strategist --pm_id 1 --once
"""
import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    parser = argparse.ArgumentParser(description="PM Strategist brain")
    parser.add_argument("--pm_id", required=True)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--trigger", default="manual", help="Trigger label for --once mode")
    args = parser.parse_args()

    from common.strategist.loop import Strategist
    s = Strategist(args.pm_id)

    if args.once:
        result = s.run_cycle(args.trigger)
        print(f"\nCycle result: {result}")
    else:
        s.run_forever()


if __name__ == "__main__":
    main()
