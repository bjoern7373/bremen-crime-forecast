"""
One-time fetch of a stitched OSM raster basemap for Bremen (covers all 19
Stadtteile), used as a static background image in the incident map artifact
(the Artifact sandbox's CSP blocks live tile requests, so a baked-in image
is the only way to show real OSM cartography there).

Respects OSM's tile usage policy: identifying User-Agent, light one-time
fetch (not a live embedded slippy map), required attribution is added by
the artifact itself, not here.

Usage: python src/fetch_basemap.py
"""
import math
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
USER_AGENT = "bremen-crime-forecast-research/0.1 (personal non-commercial research project)"

ZOOM = 13
# Covers all 19 Bremen Stadtteile with margin (Blumenthal north, Oberneuland
# east, Huchting/Arsten south, Groepelingen west).
LAT_MIN, LAT_MAX = 52.97, 53.23
LON_MIN, LON_MAX = 8.44, 9.00


def deg2tile(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2 ** zoom
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile2deg(x, y, zoom):
    n = 2 ** zoom
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad), lon_deg


def main():
    x0f, y0f = deg2tile(LAT_MAX, LON_MIN, ZOOM)  # top-left
    x1f, y1f = deg2tile(LAT_MIN, LON_MAX, ZOOM)  # bottom-right
    x0, x1 = int(math.floor(x0f)), int(math.floor(x1f))
    y0, y1 = int(math.floor(y0f)), int(math.floor(y1f))

    n_x, n_y = x1 - x0 + 1, y1 - y0 + 1
    print(f"Zoom {ZOOM}: {n_x}x{n_y} = {n_x*n_y} tiles, x[{x0}-{x1}] y[{y0}-{y1}]")

    canvas = Image.new("RGB", (n_x * 256, n_y * 256), "white")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    for yi in range(y0, y1 + 1):
        for xi in range(x0, x1 + 1):
            url = f"https://tile.openstreetmap.org/{ZOOM}/{xi}/{yi}.png"
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            from io import BytesIO
            tile_img = Image.open(BytesIO(resp.content)).convert("RGB")
            canvas.paste(tile_img, ((xi - x0) * 256, (yi - y0) * 256))

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ASSETS_DIR / "bremen_base_map.png"
    canvas.save(out_path, optimize=True)

    # exact geographic bounds of the stitched canvas (tile edges, not the
    # requested lat/lon box) -- needed later for precise marker placement.
    lat_top, lon_left = tile2deg(x0, y0, ZOOM)
    lat_bottom, lon_right = tile2deg(x1 + 1, y1 + 1, ZOOM)
    bounds = {
        "zoom": ZOOM, "width_px": n_x * 256, "height_px": n_y * 256,
        "lat_top": lat_top, "lat_bottom": lat_bottom,
        "lon_left": lon_left, "lon_right": lon_right,
    }
    import json
    (ASSETS_DIR / "bremen_base_map_bounds.json").write_text(
        json.dumps(bounds, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} ({canvas.size[0]}x{canvas.size[1]}px)")
    print(f"Bounds: {bounds}")


if __name__ == "__main__":
    main()
