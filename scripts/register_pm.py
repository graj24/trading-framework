"""CLI to register a new PM workspace.

Usage:
    python -m scripts.register_pm --id 2
    python -m scripts.register_pm --id 3 --copy-from 1
"""
import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Register a new PM workspace")
    parser.add_argument("--id", required=True, help="PM ID (e.g. 2)")
    parser.add_argument("--copy-from", default=None, help="Copy strategy from existing PM ID")
    parser.add_argument("--prompt", default=None, help="Path to PM prompt file")
    args = parser.parse_args()

    from common.core.pm_runtime import register_pm
    ws = register_pm(
        pm_id=args.id,
        prompt_path=args.prompt,
        copy_from=args.copy_from,
    )
    print(f"\nPM{args.id} registered successfully.")
    print(f"Workspace: {ws.resolve()}")
    print(f"\nDirectory layout:")
    for p in sorted(ws.rglob("*")):
        if ".git" not in str(p) and "__pycache__" not in str(p):
            indent = "  " * (len(p.relative_to(ws).parts) - 1)
            print(f"  {indent}{p.name}{'/' if p.is_dir() else ''}")


if __name__ == "__main__":
    main()
