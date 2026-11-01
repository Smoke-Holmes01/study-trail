"""v004 recovery rehearsal. Historical tools are archived outside the source repository."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from tools.storage_maintenance import backup, restore_verify

if __name__ == "__main__":
    target = backup()
    restore_verify(target)
    print("Recovery verified; run storage_maintenance.py resume when ready.")
