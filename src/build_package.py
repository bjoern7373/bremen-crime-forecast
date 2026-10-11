"""
Build the standalone package (dist/BremenTatkarte + dist/BremenTatkarte.zip)
for PCs without this project: launcher files from package/, the pages and
data files from assets/, the scripts its own update needs from src/, and our
whole archive from data/.

Everything comes from this one script, so the parts cannot drift apart --
a page that expects a data file the package doesn't have, or a launcher
that calls a script option the packaged script doesn't know. The finished
package is checked before it is zipped: its own export, run on its own data,
must reproduce exactly the files it ships.

Usage:
    python src/build_package.py
"""
from __future__ import annotations

import ast
import hashlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, ASSETS, DATA = ROOT / "src", ROOT / "assets", ROOT / "data"
PACKAGE_FILES = ROOT / "package"            # Start.bat, launch_map.ps1, serve.ps1, LIESMICH.txt
OUT = ROOT / "dist" / "BremenTatkarte"

PAGES = ["map_local.html", "details_local.html", "message_local.html"]
DATA_FILES = ["map_data.json", "forecast.json", "court_stats.json",
              "news_index.json", "police_other.json", "court_cases.json"]
# Our archive and everything derived from it. Without it the package's first
# update would rebuild the collection from the little the police site still
# offers. (data/raw_courts is left out: the package collects no court data.)
DATA_DIRS = ["raw", "archive", "geocode_cache", "processed"]
DATA_EXTRA = ["known_chunks.json", "wayback_sources.json"]


def launcher_steps() -> list[str]:
    """The scripts package/launch_map.ps1 runs, in order."""
    text = (PACKAGE_FILES / "launch_map.ps1").read_text(encoding="utf-8")
    return re.findall(r'@\("src\\([\w.]+\.py)"', text)


def with_imports(scripts: list[str]) -> list[str]:
    """The given scripts plus every module of ours they import, directly or indirectly."""
    needed, todo = [], list(scripts)
    while todo:
        name = todo.pop()
        if name in needed:
            continue
        needed.append(name)
        for node in ast.walk(ast.parse((SRC / name).read_text(encoding="utf-8"))):
            modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                       else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            todo.extend(f"{m}.py" for m in modules if (SRC / f"{m}.py").exists())
    return sorted(needed)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


ZIP = OUT.with_suffix(".zip")
SKIP = shutil.ignore_patterns("*.tmp", "__pycache__")


def build() -> None:
    missing = [str(p) for p in ([ASSETS / n for n in PAGES + DATA_FILES] + [DATA / n for n in DATA_DIRS]
                                + [PACKAGE_FILES / "launch_map.ps1", ROOT / "requirements.txt"]) if not p.exists()]
    if missing:
        raise SystemExit("Cannot build the package, these are missing:\n  " + "\n  ".join(missing))
    # the old zip goes first: if anything below fails, no outdated package is left looking valid
    ZIP.unlink(missing_ok=True)
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(PACKAGE_FILES, OUT, ignore=SKIP)
    shutil.copy2(ROOT / "requirements.txt", OUT / "requirements.txt")

    (OUT / "assets").mkdir()
    for name in PAGES + DATA_FILES:
        shutil.copy2(ASSETS / name, OUT / "assets" / name)

    (OUT / "src").mkdir()
    scripts = with_imports(launcher_steps())
    for name in scripts:
        shutil.copy2(SRC / name, OUT / "src" / name)

    (OUT / "data").mkdir()
    for name in DATA_DIRS:
        shutil.copytree(DATA / name, OUT / "data" / name, ignore=SKIP)
    for name in DATA_EXTRA:
        if (DATA / name).exists():
            shutil.copy2(DATA / name, OUT / "data" / name)
    # "just updated": the first start shows the shipped data at once instead of updating first
    (OUT / "data" / ".last_update.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    print(f"Assembled {OUT}: {len(PAGES)} pages, {len(DATA_FILES)} data files, {len(scripts)} scripts "
          f"({', '.join(scripts)})")


def check() -> None:
    """The package must be able to run its own update. Its export step, run
    offline on the data it ships, has to reproduce the data files it ships."""
    steps = launcher_steps()
    missing = [s for s in steps if not (OUT / "src" / s).exists()]
    if missing:
        raise SystemExit(f"launch_map.ps1 calls scripts the package lacks: {missing}")
    for script in sorted((OUT / "src").glob("*.py")):
        subprocess.run([sys.executable, "-m", "py_compile", str(script)], check=True)
    shipped = {name: digest(OUT / "assets" / name) for name in DATA_FILES}
    try:
        for step in ("build_dataset.py", "forecast.py", "export_map_data.py"):   # the steps that need no network
            result = subprocess.run([sys.executable, str(OUT / "src" / step)], cwd=OUT, capture_output=True, text=True)
            if result.returncode != 0:
                raise SystemExit(f"the package's own {step} fails:\n{result.stderr[-2000:]}")
    finally:
        for leftover in list(OUT.rglob("__pycache__")):
            shutil.rmtree(leftover)
    changed = [name for name in DATA_FILES if digest(OUT / "assets" / name) != shipped[name]]
    if changed:
        raise SystemExit(f"The package's own update produces different data files than it ships: {changed}.\n"
                         f"assets/ is older than data/ -- run the pipeline (run_daily.bat) first, then build again.")
    print("Checked: all launcher steps present, all scripts compile, "
          "the package's own export reproduces its data files exactly.")


def main():
    build()
    check()
    # stamped last: "just updated" from the moment the package is finished
    (OUT / "data" / ".last_update.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    archive = shutil.make_archive(str(OUT), "zip", root_dir=OUT.parent, base_dir=OUT.name)
    print(f"Wrote {archive} ({Path(archive).stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
