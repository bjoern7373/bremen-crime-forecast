"""
Known archive-chunk URLs of the Polizei Bremen press archive
(https://www.polizei.bremen.de/news/pressestelle/...).

The site has no true paginated/date-filterable archive API: the public
"Pressearchiv" overview only shows a rolling ~6-week window, and older
content only exists as individual "pressemeldungen-ab-<date>-<id>" pages,
each covering a few weeks to a few months of press releases.

This list was assembled by searching
    site:polizei.bremen.de "pressemeldungen_ab"
(and variants with a year appended) and sorting the hits chronologically.
It is necessarily incomplete -- re-run similar searches periodically and
append any new URLs you find. The most recent entry has no fixed end date
(it is still being appended to by the police press office), so re-scrape
it on every run to pick up new entries.
"""

CHUNK_URLS = [
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-14012025-62018",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-14042025-63985",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-18072025-64821",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-10092025-65280",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-01102025-65420",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-09122025-66024",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-02022026-66703",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-18022026-67198",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-26032026-67470",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-27042026-67751",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-14052026-67909",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-03072026-68314",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-22072026-68493",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-19082026-68769",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-11092026-69054",
    "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-30092026-69187",
]
