"""
Extraction of Bremen Stadtteil names from the free-text "Ort:" field of a
press release, e.g.:

    "Bremen-Vegesack, OT Fähr-Lobbendorf, Kirchhofstraße"
    "Bremen-Neustadt/Bremen-Mitte, OT Neustadt/Altstadt, Stephanibrücke"
    "Bremen – Gröpelingen, Seewenjestraße"
    "Bremen-West, Gröpelingen, OT Lindenhof, Stapelfeldtstraße"
    "Bremerhaven-Geestemünde, Bachstraße"

stadtteile_of() is the one function the pipeline uses. It only ever returns
names from KNOWN_STADTTEILE; a place line it cannot read yields nothing
rather than a guess (a street such as "Bremen, Mittelweg" or "Bremerhavener
Straße" must not be taken for a place).
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

# The Ortsteile of each Stadtteil, for place lines that name only the
# Ortsteil ("Bremen, Burgdamm, Helsingborger Straße", "Bremen-Nord, OT
# Fähr-Lobbendorf"). Ortsteile that carry the name of their own Stadtteil
# need no entry.
ORTSTEILE = {
    "Mitte": ["Altstadt", "Bahnhofsvorstadt", "Ostertor"],
    "Häfen": ["Industriehäfen", "Neustädter Hafen", "Hohentorshafen"],
    "Neustadt": ["Alte Neustadt", "Hohentor", "Südervorstadt", "Gartenstadt Süd", "Buntentor", "Neuenland",
                 "Huckelriede"],
    "Obervieland": ["Habenhausen", "Arsten", "Kattenturm", "Kattenesch"],
    "Huchting": ["Mittelshuchting", "Sodenmatt", "Kirchhuchting", "Grolland"],
    "Woltmershausen": ["Rablinghausen"],
    "Östliche Vorstadt": ["Steintor", "Fesenfeld", "Peterswerder", "Hulsberg"],
    "Schwachhausen": ["Neu-Schwachhausen", "Bürgerpark", "Barkhof", "Riensberg", "Radio Bremen", "Gete"],
    "Vahr": ["Gartenstadt Vahr", "Neue Vahr Nord", "Neue Vahr Südwest", "Neue Vahr Südost", "Neue Vahr"],
    "Horn-Lehe": ["Horn", "Lehe", "Lehesterdeich"],
    "Osterholz": ["Ellener Feld", "Ellenerbrok-Schevemoor", "Tenever", "Blockdiek"],
    "Hemelingen": ["Sebaldsbrück", "Hastedt", "Arbergen", "Mahndorf"],
    "Findorff": ["Regensburger Straße", "Findorff-Bürgerweide", "Bürgerweide", "Weidedamm", "In den Hufen"],
    "Walle": ["Utbremen", "Steffensweg", "Westend", "Osterfeuerberg", "Hohweg", "Überseestadt"],
    "Gröpelingen": ["Lindenhof", "Ohlenhof", "In den Wischen", "Oslebshausen"],
    "Burglesum": ["Burg-Grambke", "Werderland", "Burgdamm", "Lesum", "St. Magnus"],
    "Vegesack": ["Grohn", "Schönebeck", "Aumund-Hammersbeck", "Fähr-Lobbendorf"],
    "Blumenthal": ["Rönnebeck", "Lüssum-Bockhorn", "Farge", "Rekum"],
}
# Landmarks and Ortsteile distinctive enough to place running text (a court
# release that mentions "im Steintor") -- used by match_verdicts.py, which
# looks for them anywhere in a text. Not used for the "Ort:" line of police
# releases: there a stadium announcement is no incident in a Stadtteil.
LANDMARKS = {
    "weserstadion": "Östliche Vorstadt", "innenstadt": "Mitte", "altstadt": "Mitte",
    "ueberseestadt": "Walle", "überseestadt": "Walle", "arsten": "Obervieland",
    "kattenturm": "Obervieland", "huckelriede": "Neustadt", "steintor": "Östliche Vorstadt",
    "sebaldsbrueck": "Hemelingen", "sebaldsbrück": "Hemelingen",
}

_STADTTEIL_OF_PLACE = {name.lower(): name for name in KNOWN_STADTTEILE}
_STADTTEIL_OF_PLACE.update({ortsteil.lower(): stadtteil for stadtteil, names in ORTSTEILE.items() for ortsteil in names})
_STADTTEIL_OF_PLACE.update({"innenstadt": "Mitte", "ueberseestadt": "Walle", "sebaldsbrueck": "Hemelingen"})

_STADTTEIL_RE = re.compile(r"Bremen-([A-Za-zÄÖÜäöüß/\s.-]+?)(?:,|$)")
# The press office does not always write "Bremen-<Stadtteil>,": also seen are
# "Bremen Findorff", "Bremen – Gröpelingen", "Bremen - Horn-Lehe", "Bremen,
# Vegesack", "Bemen-Mitte" and "Bremen-Vahr/Bundesautobahn 27". These looser
# spellings only count when a known Stadtteil name follows -- otherwise a
# street ("Bremen, Mittelweg") would be taken for a Stadtteil.
_LOOSE_STADTTEIL_RE = re.compile(
    r"(?:Bremen|Bemen)\s*(?:[-–]\s*|,\s*|\s+)("
    + "|".join(re.escape(name) for name in sorted(KNOWN_STADTTEILE, key=len, reverse=True))
    + r")(?=\s*(?:,|/|$|-?\s*OT\b))"
)
_STARTS_WITH_BREMEN_RE = re.compile(r"\s*(?:Bremen|Bemen)\b(?!haven)")
_AREA_PREFIX_RE = re.compile(r"^(?:Bremen|Bemen)\s*(?:[-–]\s*(?:Nord|West|Süd|Ost)\b)?\s*[-–]?\s*", re.IGNORECASE)
# the city, not a street named after it ("Bremerhavener Straße" is in Walle)
_BHV_RE = re.compile(r"Bremerhaven(?!er)")


def extract_stadtteile(ort_field: str) -> list[str]:
    """Raw Stadtteil mentions of an 'Ort:' field, still to be cleaned by
    canonicalize_stadtteil(). Multiple Stadtteile joined with '/' (an
    incident spanning a border) are split into separate entries."""
    if not ort_field:
        return []
    matches = _STADTTEIL_RE.findall(ort_field)
    out = []
    for m in matches:
        for part in m.split("/"):
            name = part.strip()
            if name:
                out.append(name)
    for name in _LOOSE_STADTTEIL_RE.findall(ort_field):
        if not any(name in found for found in out):
            out.append(name)
    return out


def _place(name: str) -> str | None:
    """The Stadtteil a place name stands for -- the Stadtteil itself or one
    of its Ortsteile -- if the name is exactly that, nothing else."""
    return _STADTTEIL_OF_PLACE.get(name.strip().lower())


def canonicalize_stadtteil(raw: str) -> list[str]:
    """Best-effort cleanup of one extract_stadtteile() result into 0+ known
    Stadtteil names. extract_stadtteile's regex sometimes over-captures when
    the source text is missing an expected comma (e.g. 'Mitte OT
    Bahnhofsvorstadt' instead of 'Mitte, OT Bahnhofsvorstadt'), or when two
    places are hyphen-joined instead of slash-joined ('Mitte-Östliche
    Vorstadt', 'Obervieland-Industriehäfen'). Returns [] if nothing
    recognizable is found (city-wide mentions like 'Stadtgebiet',
    out-of-town places like 'Hamburg', etc. -- correctly unmappable to a
    single point).
    """
    name = raw.strip()
    if name in KNOWN_STADTTEILE:
        return [name]

    # "Mitte OT Bahnhofsvorstadt" / "Gröpelingen OT Lindenhof" -> take the part
    # before " OT "
    m = re.match(r"^(.+?)\s+OT\s+", name, re.IGNORECASE)
    if m and m.group(1).strip() in KNOWN_STADTTEILE:
        return [m.group(1).strip()]

    # hyphen-joined compound of two places (Horn-Lehe itself is already
    # handled by the exact-match check above)
    if "-" in name:
        parts = [_place(p) for p in name.split("-")]
        if len(parts) > 1 and all(parts):
            return list(dict.fromkeys(parts))

    # a single Ortsteil name standing in for its Stadtteil
    if _place(name):
        return [_place(name)]

    # last resort: does exactly one known Stadtteil name appear as a whole
    # phrase inside the raw text (handles stray "Bremen-Mitte" with no
    # trailing comma, typos, etc.)?
    found = {d for d in KNOWN_STADTTEILE if d.lower() in name.lower()}
    if len(found) == 1:
        return [found.pop()]

    return []


def _named_parts(ort_field: str) -> list[str]:
    """For place lines the patterns above cannot read: go through the
    comma-separated parts of a line that starts with "Bremen" (also
    "Bremen-West, ...", "Bremen-Nord, ...") and take every part that is
    exactly a Stadtteil or Ortsteil name, with or without "OT" in front --
    "Bremen-West, Gröpelingen, OT Lindenhof, Stapelfeldtstraße",
    "Bremen, Burgdamm, Helsingborger Straße", "Bremen, Mitte-Östliche Vorstadt"."""
    if not _STARTS_WITH_BREMEN_RE.match(ort_field):
        return []
    out = []
    for part in ort_field.split(","):
        part = _AREA_PREFIX_RE.sub("", part.strip())
        part = re.sub(r"^OT[\s-]*", "", part, flags=re.IGNORECASE)
        for piece in re.split(r"\s+und\s+|/", part):
            found = [_place(piece)] if _place(piece) else [_place(p) for p in piece.split("-")]
            if found and all(found):
                out.extend(found)
    return list(dict.fromkeys(out))


def stadtteile_of(ort_field: str | None) -> list[str]:
    """The Stadtteile of Bremen an 'Ort:' field names, in the order named;
    [] if it names none we recognise (or lies in Bremerhaven)."""
    ort_field = ort_field or ""
    if is_bremerhaven(ort_field):
        return []
    out = []
    for raw in extract_stadtteile(ort_field):
        for name in canonicalize_stadtteil(raw):
            if name not in out:
                out.append(name)
    return out or _named_parts(ort_field)


def is_bremerhaven(ort_field: str) -> bool:
    return bool(_BHV_RE.search(ort_field or ""))


def extract_ortsteil(ort_field: str) -> str | None:
    """Best-effort extraction of the 'OT <Ortsteil>' part, if present."""
    if not ort_field:
        return None
    m = re.search(r"OT\s+([A-Za-zÄÖÜäöüß/\s.-]+?)(?:,|$)", ort_field)
    return m.group(1).strip() if m else None
