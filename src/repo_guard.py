"""
Keeps the project inside GitHub's size limits. Runs on every daily update,
between `git add` and `git commit`.

GitHub rejects a push that contains a file over 100 MB -- and once such a
file is part of a commit, every later push is rejected too until the history
is rewritten. So a file that has grown too large is taken out of the commit
before it can get into the history (it stays on this PC, nothing is deleted);
everything else is published as usual.

    python src/repo_guard.py --check          # after `git add`, before `git commit`
    python src/repo_guard.py --pushed <code>  # after `git push`, with its exit code

Both update assets/repo_status.json, which the local map shows in its sidebar
(that file is not published).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = ROOT / "assets" / "repo_status.json"
SITE_DIR = "docs"

MB = 1024 * 1024
FILE_LIMIT_MB = 100   # GitHub rejects larger files
FILE_BLOCK_MB = 50    # GitHub starts warning here -- we stop before that
FILE_WARN_MB = 25
REPO_LIMIT_MB = 1000  # GitHub's recommended maximum for a repository
REPO_WARN_MB = 500
SITE_LIMIT_MB = 1000  # maximum size of a published GitHub Pages site
SITE_WARN_MB = 500


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def _paths(*git_args: str) -> list[str]:
    return [p for p in _git(*git_args).split("\0") if p]


def _size(path: str) -> int:
    full = ROOT / path
    return full.stat().st_size if full.is_file() else 0


def repo_size_mb() -> float:
    """Size of the whole history on this PC. GitHub stores it a little more
    compactly, so this errs on the cautious side."""
    fields = dict(line.split(": ", 1) for line in _git("count-objects", "-v").splitlines())
    return (int(fields["size"]) + int(fields["size-pack"])) / 1024


def _mb(n_bytes: int) -> str:
    return f"{n_bytes / MB:.0f} MB"


def check() -> dict:
    """Take oversized files out of the pending commit and measure where we
    stand against the limits."""
    held_back, warnings = [], []
    for path in _paths("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"):
        size = _size(path)
        if size >= FILE_BLOCK_MB * MB:
            _git("reset", "-q", "--", path)
            held_back.append(path)
            warnings.append(f"{path} ist {_mb(size)} groß und wurde nicht zu GitHub hochgeladen "
                            f"(GitHub lehnt Dateien über {FILE_LIMIT_MB} MB ab). Die Datei liegt "
                            f"weiterhin auf diesem PC.")
        elif size >= FILE_WARN_MB * MB:
            warnings.append(f"{path} ist {_mb(size)} groß – ab {FILE_BLOCK_MB} MB wird sie nicht "
                            f"mehr hochgeladen.")

    sizes = {p: _size(p) for p in _paths("ls-files", "-z") if p not in held_back}
    largest = max(sizes, key=sizes.get) if sizes else None
    site_bytes = sum(s for p, s in sizes.items() if p.startswith(SITE_DIR + "/"))
    repo_mb = repo_size_mb()
    if repo_mb >= REPO_WARN_MB:
        warnings.append(f"Das Projekt belegt {repo_mb:.0f} MB bei GitHub – empfohlen sind höchstens "
                        f"{REPO_LIMIT_MB} MB.")
    if site_bytes >= SITE_WARN_MB * MB:
        warnings.append(f"Die öffentliche Karte belegt {_mb(site_bytes)} – erlaubt sind "
                        f"{SITE_LIMIT_MB} MB.")

    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M"),
        "repo_mb": round(repo_mb, 1),
        "repo_limit_mb": REPO_LIMIT_MB,
        "largest_file": largest,
        "largest_file_mb": round(sizes[largest] / MB, 1) if largest else 0,
        "file_limit_mb": FILE_LIMIT_MB,
        "site_mb": round(site_bytes / MB, 1),
        "site_limit_mb": SITE_LIMIT_MB,
        "held_back": held_back,
        "warnings": warnings,
    }


PUBLISHED_PATHS = ("data", "docs/*.json", "assets/*.json")   # what the daily update commits


def left_behind(held_back: list[str] | None = None) -> str | None:
    """`git push` also reports success when there was nothing to send --
    e.g. because `git add` or `git commit` failed before it (a stale
    .git/index.lock is enough). So "uploaded" is only believed if nothing
    publishable is still waiting: no uncommitted change in what the update
    publishes, no commit that GitHub doesn't have. Returns what is waiting,
    or None."""
    try:
        waiting = [line[3:].strip().strip('"') for line in
                   _git("status", "--porcelain", "--untracked-files=all", "--", *PUBLISHED_PATHS).splitlines()
                   if line.strip()]
        # a file deliberately kept out of the commit for its size is not a failed upload
        if [path for path in waiting if path not in (held_back or [])]:
            return "Änderungen konnten nicht eingetragen werden (git add/commit fehlgeschlagen)"
        if _git("rev-list", "@{u}..HEAD").strip():
            return "Eingetragene Änderungen wurden nicht zu GitHub übertragen"
    except subprocess.CalledProcessError as e:
        return f"Stand des Uploads nicht prüfbar ({(e.stderr or '').strip()[:80]})"
    return None


def pushed(exit_code: int, status: dict, waiting: str | None = None) -> dict:
    """Remember whether the upload to GitHub worked, so a failing upload is
    visible in the map instead of only in a log file."""
    now = time.strftime("%Y-%m-%d %H:%M")
    if exit_code == 0 and not waiting:
        status.update(last_push_ok=True, last_push_at=now, push_failing_since=None, push_problem=None)
    else:
        status.update(last_push_ok=False, push_problem=waiting,
                      push_failing_since=status.get("push_failing_since") or now)
    return status


def load_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_status(status: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="run after `git add`: hold back oversized files, measure sizes")
    group.add_argument("--pushed", type=int, metavar="EXIT_CODE",
                       help="run after `git push` with its exit code")
    args = parser.parse_args()

    status = load_status()
    if args.check:
        status.update(check())
        print(f"GitHub size check: repository {status['repo_mb']} of {REPO_LIMIT_MB} MB, "
              f"largest file {status['largest_file']} {status['largest_file_mb']} of {FILE_LIMIT_MB} MB, "
              f"public site {status['site_mb']} of {SITE_LIMIT_MB} MB")
        for w in status["warnings"]:
            print(f"  WARNING: {w}")
    else:
        pushed(args.pushed, status, left_behind(status.get("held_back")))
        print("Upload to GitHub: " + ("ok" if status["last_push_ok"] else
                                      f"FAILED (failing since {status['push_failing_since']}"
                                      + (f"; {status['push_problem']}" if status.get("push_problem") else "") + ")"))
    save_status(status)


if __name__ == "__main__":
    main()
