import argparse
import logging
from radar.report import daily, weekly

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily or weekly researcher digest")
    parser.add_argument("mode", choices=["daily", "weekly"])
    parser.add_argument("--dry-run", action="store_true", help="Generate digest, print, do not send or save")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    (daily if args.mode == "daily" else weekly)(dry_run=args.dry_run)
