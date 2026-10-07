"""
Desktop launcher for the Bremen Tatkarte: refreshes the data if the last
update was more than 24h ago, makes sure a local server is serving the
assets folder, then opens the map and a details side-window, positioned
next to each other.

Invoked by the desktop shortcut (see src/make_shortcut.py).
"""
import ctypes
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
PYTHON = r"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe"
PORT = 8731
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def port_open(port, host="127.0.0.1", timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def serves_our_assets(port, timeout=1.0):
    """True only if the given port answers with OUR map_data.json -- not just
    any TCP listener. A stray server left over from testing/debugging (wrong
    folder, or just stuck) otherwise looks "available" and gets silently
    reused, serving stale or wrong content with no visible error."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/map_data.json", timeout=timeout) as r:
            if r.status != 200:
                return False
            head = r.read(16)
            return head.lstrip().startswith(b"[")
    except Exception:
        return False


def ensure_server() -> int:
    """Returns the port our own server is confirmed serving assets on.
    Never trusts an existing listener without verifying it actually serves
    OUR content -- finds a fresh port instead if the default one is taken by
    something else (or isn't responding correctly).
    """
    if port_open(PORT) and serves_our_assets(PORT):
        return PORT

    port = PORT
    for _ in range(20):
        if not port_open(port):
            break
        port += 1
    else:
        raise RuntimeError("No free port found near 8731")

    subprocess.Popen(
        [PYTHON, str(ROOT / "src" / "serve_no_cache.py"), str(port)],
        cwd=str(ASSETS_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(30):
        if serves_our_assets(port):
            return port
        time.sleep(0.2)
    raise RuntimeError(f"Server on port {port} did not come up in time")


def maybe_update():
    gate = subprocess.run([PYTHON, str(ROOT / "src" / "update_gate.py")], capture_output=True, text=True)
    print(gate.stdout.strip())
    if gate.returncode == 0:
        print("Running update pipeline (this may take a few minutes)...")
        subprocess.run([str(ROOT / "run_daily.bat")], cwd=str(ROOT), shell=True)


def screen_size():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def open_window(url, x, y, w, h):
    subprocess.Popen([
        EDGE, "--new-window",
        f"--window-position={x},{y}", f"--window-size={w},{h}",
        url,
    ])


def main():
    maybe_update()
    port = ensure_server()

    sw, sh = screen_size()
    h = sh - 80

    # Only the map window is opened from here. It opens the details window
    # itself via window.open() on a real page-load/click -- coordinating two
    # independent msedge.exe command-line launches was unreliable (Edge
    # doesn't consistently honor --window-position/--window-size for a
    # second invocation once it's already running, and the two windows could
    # even land as tabs of the same window instead of separate ones).
    base = f"http://localhost:{port}"
    open_window(f"{base}/map_local.html", 0, 0, int(sw * 0.65), h)


if __name__ == "__main__":
    main()
