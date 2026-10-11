"""
Checks that a file too large for GitHub can never get into a commit, and
that everything else still does. Uses a throwaway git repository.

Run:  python tests/test_repo_guard.py     (or: pytest tests)
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import repo_guard

KB = 1024


class Repo:
    """A fresh git repository with the limits scaled down from MB to KB."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="guard-test-"))
        repo_guard.ROOT = self.dir
        repo_guard.STATUS_PATH = self.dir / "assets" / "repo_status.json"
        repo_guard.MB = KB
        self.git("init", "-q")
        self.write("docs/index.html", 1 * KB)
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.dir, capture_output=True, text=True,
                              check=True).stdout

    def write(self, path, size):
        full = self.dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"x" * size)

    def staged(self):
        return sorted(self.git("diff", "--cached", "--name-only").split())


def test_oversized_new_file_is_held_back_and_the_rest_is_committed():
    repo = Repo()
    repo.write("data/ok.csv", 10 * KB)
    repo.write("data/huge.bin", 60 * KB)            # over the 50 "MB" block limit
    repo.git("add", "data")
    status = repo_guard.check()
    assert repo.staged() == ["data/ok.csv"]
    assert status["held_back"] == ["data/huge.bin"]
    assert (repo.dir / "data/huge.bin").exists()    # held back, not deleted
    assert any("huge.bin" in w for w in status["warnings"])


def test_tracked_file_that_grew_too_large_keeps_its_old_version_in_the_commit():
    repo = Repo()
    repo.write("data/grows.jsonl", 10 * KB)
    repo.git("add", "data")
    repo.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "small")
    repo.write("data/grows.jsonl", 70 * KB)
    repo.git("add", "data")
    status = repo_guard.check()
    assert repo.staged() == []
    assert status["held_back"] == ["data/grows.jsonl"]
    assert "data/grows.jsonl" in repo.git("ls-files").split()   # still tracked, old version


def test_large_but_allowed_file_only_warns():
    repo = Repo()
    repo.write("data/big.csv", 30 * KB)             # between warn (25) and block (50)
    repo.git("add", "data")
    status = repo_guard.check()
    assert repo.staged() == ["data/big.csv"]
    assert status["held_back"] == []
    assert len(status["warnings"]) == 1


def test_normal_update_reports_sizes_without_warnings():
    repo = Repo()
    repo.write("data/a.csv", 3 * KB)
    repo.write("docs/map_data.json", 2 * KB)
    repo.git("add", ".")
    status = repo_guard.check()
    assert status["warnings"] == [] and status["held_back"] == []
    assert status["largest_file"] == "data/a.csv"
    assert status["site_mb"] == 3.0                 # docs/index.html + docs/map_data.json


def test_repository_size_counts_what_git_has_stored():
    repo = Repo()
    before = repo_guard.repo_size_mb()
    full = repo.dir / "data" / "random.bin"
    full.parent.mkdir(parents=True)
    full.write_bytes(os.urandom(300 * KB))          # incompressible, so git really stores ~300 KB
    repo.git("add", "data")
    assert 0.25 < repo_guard.repo_size_mb() - before < 0.4


def test_failed_upload_is_remembered_until_one_succeeds():
    status = repo_guard.pushed(1, {})
    assert status["last_push_ok"] is False
    first_failure = status["push_failing_since"]
    assert first_failure
    status = repo_guard.pushed(128, status)
    assert status["push_failing_since"] == first_failure
    status = repo_guard.pushed(0, status)
    assert status["last_push_ok"] is True and status["push_failing_since"] is None


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:  # noqa: BLE001 -- report every failure, then exit non-zero
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} of {len(tests)} passed")
    sys.exit(1 if failed else 0)
