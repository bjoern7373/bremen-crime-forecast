"""
Rule-based Delikttyp (offense category) classifier for Polizei Bremen press
releases. Deliberately simple keyword matching, not ML -- press releases are
short, formulaic and written by the same press office, so keyword rules get
reasonable precision without a labeled training set.

Categories are checked in order; the first match wins. Order matters:
more specific categories (Raub, Toetungsdelikt) are checked before generic
ones (Koerperverletzung) that would otherwise also match.
"""
import re

# (category, [regex patterns]) -- checked in order, case-insensitive.
_RULES: list[tuple[str, list[str]]] = [
    ("Toetungsdelikt", [
        r"\btot(e|en)?\b", r"leiche", r"t[öo]dlich", r"mordkommission",
        r"erschossen", r"get[öo]tet",
    ]),
    ("Raub", [
        r"\braub\b", r"raubten", r"überfiel", r"überfielen", r"überfallen",
        r"ausgeraubt", r"räuber",
    ]),
    ("Einbruch", [
        r"einbruch", r"einbrecher", r"einbrach", r"eingebrochen",
        r"hebelte.*t[üu]r", r"aufgehebelt",
    ]),
    ("Fahrzeugaufbruch", [
        r"auto(s)? aufgebrochen", r"fahrzeug(e)? aufgebrochen",
        r"autoaufbrecher",
    ]),
    ("Sexualdelikt", [
        r"sexuelle(n|r)? (n[öo]tigung|bel[äa]stigung|übergriff)",
        r"vergewaltig", r"exhibitionist",
    ]),
    ("Waffendelikt", [
        r"schusswaffe", r"schuss(abgabe|verletzung)", r"angeschossen",
        r"mit messer", r"messerverletzung", r"dienstwaffe",
    ]),
    ("Hasskriminalitaet", [
        r"antisemitisch", r"homophob", r"rassistisch", r"sexuelle(n)? orientierung",
        r"fremdenfeindlich",
    ]),
    ("Brandstiftung", [
        r"brandstiftung", r"brandsätze", r"in brand gesetzt", r"anz[üu]ndete",
    ]),
    ("Drogendelikt", [
        r"marihuana", r"kokain", r"cannabis", r"betäubungsmittel",
        r"drogendealer", r"drogenfahrt", r"rauschgift",
    ]),
    ("Koerperverletzung", [
        r"k[öo]rperverletzung", r"schlug", r"schlugen", r"attackiert",
        r"angegriffen", r"verprügelt", r"schlägerei", r"auseinandersetzung",
    ]),
    ("Sachbeschaedigung", [
        r"sachbeschädigung", r"vandalismus", r"graffiti", r"sprayer",
        r"beschädigt(en)?",
    ]),
    ("Diebstahl", [
        r"diebstahl", r"\bdieb(e)?\b", r"entwendet", r"gestohlen", r"taschendieb",
    ]),
    ("Verkehrsunfall", [
        r"verkehrsunfall", r"kollidiert", r"fahrzeugführer verletzt",
        r"unfallflucht",
    ]),
]

_COMPILED = [(cat, [re.compile(p, re.IGNORECASE) for p in pats]) for cat, pats in _RULES]


def classify(title: str, text: str) -> str:
    haystack = f"{title}\n{text}"
    for category, patterns in _COMPILED:
        if any(p.search(haystack) for p in patterns):
            return category
    return "Sonstiges"
