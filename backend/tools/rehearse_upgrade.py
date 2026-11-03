"""Coordinated backup and independent restore rehearsal for the selected profile."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from tools.storage_maintenance import backup, restore_verify

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-test-source", action="store_true")
    args = parser.parse_args()
    target = backup(allow_test_source=args.allow_test_source)
    restore_verify(target)
    print("Recovery verified; run storage_maintenance.py resume when ready.")
