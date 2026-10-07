"""
24h update gate for the desktop launcher (src/launch_map.py). The scheduled
Windows task already runs daily regardless; this is purely so that double-
clicking the desktop shortcut several times a day (e.g. after a reboot)
doesn't re-trigger a full scrape/geocode every single time.

Usage:
    python src/update_gate.py            exit 0 ("update needed") if no
                                           timestamp file exists or it's
                                           older than 24h (PC-Zeit); exit 1
                                           otherwise. Prints UPDATE_NEEDED /
                                           UP_TO_DATE.
    python src/update_gate.py --mark-done writes the current timestamp.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP_PATH = ROOT / "data" / ".last_update.txt"
MAX_AGE = timedelta(hours=24)


def main():
    if "--mark-done" in sys.argv:
        STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        STAMP_PATH.write_text(datetime.now().isoformat(), encoding="utf-8")
        print(f"Marked update done at {datetime.now().isoformat()}")
        return

    if not STAMP_PATH.exists():
        print("UPDATE_NEEDED (no previous timestamp)")
        sys.exit(0)

    try:
        last = datetime.fromisoformat(STAMP_PATH.read_text(encoding="utf-8").strip())
    except ValueError:
        print("UPDATE_NEEDED (unreadable timestamp)")
        sys.exit(0)

    age = datetime.now() - last
    if age > MAX_AGE:
        print(f"UPDATE_NEEDED (last update {age} ago)")
        sys.exit(0)
    else:
        print(f"UP_TO_DATE (last update {age} ago)")
        sys.exit(1)


if __name__ == "__main__":
    main()
