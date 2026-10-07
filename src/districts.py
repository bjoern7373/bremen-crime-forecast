"""
Extraction of Bremen Stadtteil/Ortsteil names from the free-text "Ort:" field
of a press release, e.g.:

    "Bremen-Vegesack, OT Fähr-Lobbendorf, Kirchhofstraße"
    "Bremen-Neustadt/Bremen-Mitte, OT Neustadt/Altstadt, Stephanibrücke"
    "Bremerhaven-Geestemünde, Bachstraße"

Not exhaustive/authoritative -- used to sanity-check extraction, not to
reject unknown names (new/renamed Ortsteile, OCR-ish typos in press
releases, etc. should still pass through).
"""
import re

# Bremen's 23 Stadtteile (city of Bremen proper; Bremerhaven is a separate city)
KNOWN_STADTTEILE = {
    "Mitte", "Östliche Vorstadt", "Findorff", "Walle", "Gröpelingen", "Häfen",
    "Vegesack", "Burglesum", "Blumenthal", "Schwachhausen", "Vahr",
    "Horn-Lehe", "Oberneuland", "Osterholz", "Hemelingen", "Woltmershausen",
    "Huchting", "Obervieland", "Neustadt",
    "Borgfeld", "Blockland", "Seehausen", "Strom",
}

# Well-known Ortsteile/landmarks that are unambiguous enough to map straight
# to their Stadtteil when the press text names only the Ortsteil/landmark
# (no "Bremen-<Stadtteil>" prefix at all).
_ORTSTEIL_ALIASES = {
    "weserstadion": "Mitte",
    "innenstadt": "Mitte",
    "altstadt": "Mitte",
    "ueberseestadt": "Walle",
    "überseestadt": "Walle",
    "arsten": "Obervieland",
    "kattenturm": "Obervieland",
    "huckelriede": "Neustadt",
    "steintor": "Östliche Vorstadt",
    "sebaldsbrueck": "Hemelingen",
    "sebaldsbrück": "Hemelingen",
}

_STADTTEIL_RE = re.compile(r"Bremen-([A-Za-zÄÖÜäöüß/\s.-]+?)(?:,|$)")
_BHV_RE = re.compile(r"Bremerhaven")


def extract_stadtteile(ort_field: str) -> list[str]:
    """Return the list of Stadtteil names mentioned in an 'Ort:' field.

    Multiple Stadtteile joined with '/' (an incident spanning a border, or an
    ambiguous description) are split into separate entries. Returns an empty
    list if no 'Bremen-<Stadtteil>' pattern is found (e.g. pure Bremerhaven
    incidents, or edge cases in the free text).
    """
    if not ort_field:
        return []
    matches = _STADTTEIL_RE.findall(ort_field)
    out = []
    for m in matches:
        for part in m.split("/"):
            name = part.strip()
            if name:
                out.append(name)
    return out


def canonicalize_stadtteil(raw: str) -> list[str]:
    """Best-effort cleanup of one extract_stadtteile() result into 0+ known
    Stadtteil names. extract_stadtteile's regex sometimes over-captures when
    the source text is missing an expected comma (e.g. 'Mitte OT
    Bahnhofsvorstadt' instead of 'Mitte, OT Bahnhofsvorstadt'), or when two
    Stadtteile are hyphen-joined instead of slash-joined ('Mitte-Östliche
    Vorstadt'). Returns [] if nothing recognizable is found (city-wide
    mentions like 'Stadtgebiet', out-of-town places like 'Hamburg', etc. --
    correctly unmappable to a single point).
    """
    name = raw.strip()
    if name in KNOWN_STADTTEILE:
        return [name]

    # "Mitte OT Bahnhofsvorstadt" / "Gröpelingen OT Lindenhof" -> take the part
    # before " OT "
    m = re.match(r"^(.+?)\s+OT\s+", name, re.IGNORECASE)
    if m and m.group(1).strip() in KNOWN_STADTTEILE:
        return [m.group(1).strip()]

    # hyphen-joined compound of two known Stadtteile (Horn-Lehe itself is
    # already handled by the exact-match check above, so this only fires for
    # genuine two-district mentions like "Gröpelingen-Walle")
    if "-" in name:
        parts = [p.strip() for p in name.split("-")]
        if all(p in KNOWN_STADTTEILE for p in parts) and len(parts) > 1:
            return parts

    # single well-known Ortsteil/landmark name standing in for its Stadtteil
    alias = _ORTSTEIL_ALIASES.get(name.lower())
    if alias:
        return [alias]

    # last resort: does exactly one known Stadtteil name appear as a whole
    # phrase inside the raw text (handles stray "Bremen-Mitte" with no
    # trailing comma, typos, etc.)?
    found = {d for d in KNOWN_STADTTEILE if d.lower() in name.lower()}
    if len(found) == 1:
        return [found.pop()]

    return []


def is_bremerhaven(ort_field: str) -> bool:
    return bool(_BHV_RE.search(ort_field or ""))


def extract_ortsteil(ort_field: str) -> str | None:
    """Best-effort extraction of the 'OT <Ortsteil>' part, if present."""
    if not ort_field:
        return None
    m = re.search(r"OT\s+([A-Za-zÄÖÜäöüß/\s.-]+?)(?:,|$)", ort_field)
    return m.group(1).strip() if m else None
