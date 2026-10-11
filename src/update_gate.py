"""
24h update gate for the desktop launcher (src/launch_map.py). The scheduled
Windows task already runs daily regardless; this is purely so that double-
clicking the desktop shortcut several times a day (e.g. after a reboot)
doesn't re-trigger a full scrape/geocode every single time.

Usage:
    python src/update_gate.py               exit 0 ("update needed") if the last
                                            completed update is older than 24h
                                            (PC-Zeit) AND the last attempt is
                                            older than 2h; exit 1 otherwise.
                                            Prints UPDATE_NEEDED / UP_TO_DATE /
                                            RETRY_LATER.
    python src/update_gate.py --mark-done   an update completed just now.
    python src/update_gate.py --mark-attempt  an update was started just now.

An update that could not reach the police site does not count as done (it is
retried), but not on every single start of the map: each attempt takes a
minute or two before the map opens, so a failed one is repeated after two
hours at the earliest.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP_PATH = ROOT / "data" / ".last_update.txt"
ATTEMPT_PATH = ROOT / "data" / ".last_attempt.txt"
MAX_AGE = timedelta(hours=24)
RETRY_AFTER = timedelta(hours=2)


def _age(path: Path) -> timedelta | None:
    """How long ago the stamp was written; None if there is no usable stamp."""
    try:
        return datetime.now() - datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _mark(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now().isoformat(), encoding="utf-8")


def main():
    if "--mark-done" in sys.argv:
        _mark(STAMP_PATH)
        print(f"Marked update done at {datetime.now().isoformat()}")
        return
    if "--mark-attempt" in sys.argv:
        _mark(ATTEMPT_PATH)
        return

    done, attempt = _age(STAMP_PATH), _age(ATTEMPT_PATH)
    if done is not None and timedelta(0) <= done <= MAX_AGE:
        print(f"UP_TO_DATE (last update {done} ago)")
        sys.exit(1)
    if attempt is not None and timedelta(0) <= attempt <= RETRY_AFTER:
        print(f"RETRY_LATER (last attempt {attempt} ago did not complete)")
        sys.exit(1)
    print("UPDATE_NEEDED (no previous timestamp)" if done is None else f"UPDATE_NEEDED (last update {done} ago)")
    sys.exit(0)


if __name__ == "__main__":
    main()
