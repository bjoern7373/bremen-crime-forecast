"""
Checks the reading lists of the map's "Meldungen" panel (src/export_news.py):
every release is listed exactly once, newest first, under a key that does not
change from day to day, and points at the right full text.

Run:  python tests/test_export_news.py     (or: pytest tests)
"""
import atexit
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import export_news


def release(pm_id, title, publish_date="07.10.2026", text="Text"):
    return {"pm_id": pm_id, "title": title, "publish_date": publish_date, "ort_raw": "Bremen-Mitte",
            "zeit_raw": "06.10.2026, 10 Uhr", "text": text, "source_url": "https://example.org/page"}


def frames(rows):
    """rows: (pm_id as pandas reads it back from the CSV, title, stadtteil or None, category, on_map
    [, publish_date]) -> (incidents, geo)"""
    incidents = pd.DataFrame([{"pm_id": r[0], "title": r[1], "stadtteil": r[2], "category": r[3],
                               "incident_date": "2026-10-06", "is_bremerhaven": False, "pub": "2026-10-07",
                               "publish_date": r[5] if len(r) > 5 else "07.10.2026"} for r in rows])
    geo = incidents[[r[4] for r in rows]].reset_index(drop=True)
    return incidents, geo


def police(releases, rows):
    incidents, geo = frames(rows)
    return export_news.build_police(releases, incidents, geo, export_news.key_maker(releases))


# ---- police list -------------------------------------------------------------

def test_key_is_the_release_number_however_the_csv_stores_it():
    index, other = police([release("002825", "Raub in Walle")], [(2825.0, "Raub in Walle", "Walle", "Raub", True)])
    assert index == [{"k": "2825", "d": "2026-10-07", "t": "Raub in Walle", "c": "Raub", "s": "Walle", "m": 1}]
    assert other == {}


def test_key_does_not_depend_on_which_other_releases_exist():
    rows = [(2825.0, "Raub in Walle", "Walle", "Raub", True)]
    today, _ = police([release("002825", "Raub in Walle")], rows)
    more = [release("000127", "Neujahr"), release("002825", "Raub in Walle"), release("009926", "Später")]
    tomorrow, _ = police(more, rows + [(127.0, "Neujahr", "Mitte", "Raub", True), (9926.0, "Später", "Mitte", "Raub", True)])
    assert today[0]["k"] == "2825"
    assert [e["k"] for e in tomorrow if e["t"] == "Raub in Walle"] == ["2825"]


def test_release_in_two_stadtteile_is_listed_once():
    index, _ = police([release("000001", "Kontrollen")],
                      [(1.0, "Kontrollen", "Mitte", "Drogendelikt", True),
                       (1.0, "Kontrollen", "Östliche Vorstadt", "Drogendelikt", True)])
    assert len(index) == 1 and index[0]["s"] == "Mitte / Östliche Vorstadt" and index[0]["m"] == 1


def test_release_that_is_not_on_the_map_keeps_its_full_text():
    index, other = police(
        [release("000005", "Falsche Wasserwerker", text="der ganze Text"), release("000006", "Raub")],
        [(5.0, "Falsche Wasserwerker", None, "Diebstahl", False), (6.0, "Raub", "Mitte", "Raub", True)])
    assert "m" not in next(e for e in index if e["k"] == "5")
    assert other == {"5": {"title": "Falsche Wasserwerker", "pub": "2026-10-07", "cat": "Diebstahl", "stadtteil": "",
                           "ort": "Bremen-Mitte", "zeit": "06.10.2026, 10 Uhr", "text": "der ganze Text",
                           "source_url": "https://example.org/page"}}   # mapped releases: text is in map_data.json


def test_releases_sharing_a_title_are_told_apart_by_their_number():
    index, other = police(
        [release("010026", "Einbrecher gestellt", text="in Walle"), release("020026", "Einbrecher gestellt", text="in Huchting")],
        [(10026.0, "Einbrecher gestellt", "Walle", "Einbruch", True),
         (20026.0, "Einbrecher gestellt", None, "Einbruch", False)])
    by_key = {e["k"]: e for e in index}
    assert by_key["10026"]["s"] == "Walle" and by_key["10026"].get("m") == 1
    assert "m" not in by_key["20026"] and other["20026"]["text"] == "in Huchting"


def test_number_used_twice_or_missing_still_gives_every_release_its_own_key():
    releases = [release("000007", "Erste"), release("000007", "Zweite"), release("", "Ohne Nummer")]
    index, _ = police(releases, [(7.0, "Erste", "Mitte", "Raub", True), (7.0, "Zweite", "Walle", "Einbruch", True),
                                 (float("nan"), "Ohne Nummer", "Mitte", "Raub", True)])
    by_title = {e["t"]: e for e in index}
    assert len({e["k"] for e in index}) == 3
    assert by_title["Erste"]["k"] == "7"                       # who had the number first keeps its key ...
    assert by_title["Zweite"]["k"].startswith("7~")            # ... the later one gets a suffix
    assert by_title["Ohne Nummer"]["k"] == "t:Ohne Nummer"
    assert by_title["Zweite"]["s"] == "Walle" and by_title["Erste"]["s"] == "Mitte"


def test_key_of_a_release_survives_a_second_release_with_its_number():
    before = export_news.key_maker([release("019126", "Hinweisportal", "18.03.2026")])
    both = export_news.key_maker([release("019126", "Hinweisportal", "18.03.2026"),
                                  release("019126", "Später, versehentlich dieselbe Nummer", "02.10.2026")])
    assert before("019126", "Hinweisportal", "18.03.2026") == both("019126", "Hinweisportal", "18.03.2026") == "19126"


def test_newest_first_and_releases_without_any_date_come_last():
    releases = [release("000125", "alt", "01.02.2025"), release("050026", "neu", "07.10.2026"),
                release("050126", "gleicher Tag, spätere Nummer", "07.10.2026"), release("", "ohne Datum", None)]
    incidents, geo = frames([])
    index, _ = export_news.build_police(releases, pd.DataFrame(columns=incidents.columns), geo,
                                        export_news.key_maker(releases))
    assert [e["t"] for e in index] == ["gleicher Tag, spätere Nummer", "neu", "alt", "ohne Datum"]


def test_mistyped_or_missing_date_takes_the_date_of_the_releases_numbered_around_it():
    releases = [release("061326", "a", "12.09.2026"), release("061426", "b", "13.09.2026"),
                release("061526", "c", "14.09.2026"), release("061626", "falscher Monat", "14.08.2026"),
                release("061726", "e", "15.09.2026"), release("061826", "Datum fehlt", None),
                release("061926", "g", "16.09.2026")]
    index, _ = police(releases, [])
    assert [(e["t"], e["d"]) for e in index] == [
        ("g", "2026-09-16"), ("Datum fehlt", "2026-09-15"), ("e", "2026-09-15"),
        ("falscher Monat", "2026-09-14"), ("c", "2026-09-14"), ("b", "2026-09-13"), ("a", "2026-09-12")]


def test_date_that_fits_its_own_archive_page_is_kept_even_if_the_number_is_odd():
    # two digits of the NUMBER swapped: the release sits among March numbers, but its date is right
    releases = [release("016526", "a", "05.03.2026"), release("016626", "b", "06.03.2026"),
                dict(release("016726", "Nummer vertippt", "15.09.2026"),
                     source_url="https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-11092026-69054"),
                release("016826", "d", "07.03.2026"), release("016926", "e", "08.03.2026")]
    index, _ = police(releases, [])
    assert next(e for e in index if e["t"] == "Nummer vertippt")["d"] == "2026-09-15"


def test_date_that_cannot_be_right_for_its_page_is_repaired():
    releases = [release("061526", "c", "14.09.2026"),
                dict(release("061626", "falscher Monat", "14.08.2026"),
                     source_url="https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-11092026-69054"),
                release("061726", "e", "15.09.2026")]
    index, _ = police(releases, [])
    assert next(e for e in index if e["t"] == "falscher Monat")["d"] == "2026-09-14"


def test_publication_day_in_the_future_is_treated_as_mistyped():
    from datetime import date
    releases = [release("061526", "c", "14.09.2026"), release("061626", "Jahr vertippt", "14.09.2027"),
                release("061726", "e", "15.09.2026")]
    days = export_news.release_days(releases, export_news.key_maker(releases), today=date(2026, 10, 11))
    assert days == {"61526": "2026-09-14", "61626": "2026-09-14", "61726": "2026-09-15"}


def test_a_gap_in_our_archive_is_not_mistaken_for_a_mistyped_date():
    releases = [release("010026", "a", "01.02.2026"), release("010126", "b", "02.02.2026"),
                release("010226", "c", "03.02.2026"),      # ... weeks we hold no releases for ...
                release("018026", "d", "20.03.2026"), release("018126", "e", "21.03.2026")]
    index, _ = police(releases, [])
    assert {e["t"]: e["d"] for e in index} == {"a": "2026-02-01", "b": "2026-02-02", "c": "2026-02-03",
                                                "d": "2026-03-20", "e": "2026-03-21"}


def test_without_numbered_neighbours_a_missing_date_falls_back_to_the_incident_date():
    index, _ = police([release("000009", "Tippfehler im Datum", "(11.02.206)")],
                      [(9.0, "Tippfehler im Datum", "Mitte", "Raub", True, "(11.02.206)")])
    assert index[0]["d"] == "2026-10-06"


# ---- court texts -------------------------------------------------------------

def test_pdf_line_breaks_are_joined_without_changing_the_wording():
    text = ("Landgericht Bremen \n - Pressestelle -  \nFreie \nHansestadt \nBremen \n \n"
            "Der 30-\njährige Angeklagte soll in der Hans-Sachs-\nStraße in Bre-\nmen einen Ein- \n"
            "und Ausgang sowie eine E-Mail-\nadresse und Vor-\nsowie Nachteile versperrt haben.\n \n \nZweiter Absatz.")
    assert export_news.reflow(text) == (
        "Landgericht Bremen - Pressestelle - Freie Hansestadt Bremen\n\n"
        "Der 30-jährige Angeklagte soll in der Hans-Sachs-Straße in Bremen einen Ein- "
        "und Ausgang sowie eine E-Mail-adresse und Vor- sowie Nachteile versperrt haben.\n\nZweiter Absatz.")


def test_hearing_dates_stay_one_per_line():
    text = ("Die Hauptverhandlung wird fortgesetzt am \nDienstag, den 13. Oktober 2026, \n"
            "Mittwoch, den 28. Oktober 2026, 14:00 Uhr, \nMontag, den 02. November 2026, 14:00 Uhr.")
    assert export_news.reflow(text) == ("Die Hauptverhandlung wird fortgesetzt am Dienstag, den 13. Oktober 2026,\n"
                                        "Mittwoch, den 28. Oktober 2026, 14:00 Uhr,\nMontag, den 02. November 2026, 14:00 Uhr.")


def test_weekday_at_the_start_of_a_line_does_not_break_a_running_sentence():
    text = ("Der Auftakt der Hauptverhandlung wird vom 13. Januar 2026 auf den kommenden\n"
            "Dienstag, den 20. Januar 2026, verlegt, weil ein Verteidiger so-\nwie ein Schöffe erkrankt sind.")
    assert export_news.reflow(text) == ("Der Auftakt der Hauptverhandlung wird vom 13. Januar 2026 auf den kommenden "
                                        "Dienstag, den 20. Januar 2026, verlegt, weil ein Verteidiger sowie ein Schöffe erkrankt sind.")


def test_short_line_ending_a_sentence_keeps_its_line_break():
    text = ("Aus der PM 43/2026:\nDie Staatsanwaltschaft wirft dem Angeklagten vor, am Abend des 17.03.2026 in der\n"
            "Kornstraße gemeinschaftlich gehandelt zu haben.")
    assert export_news.reflow(text) == ("Aus der PM 43/2026:\nDie Staatsanwaltschaft wirft dem Angeklagten vor, "
                                        "am Abend des 17.03.2026 in der Kornstraße gemeinschaftlich gehandelt zu haben.")


def test_stage_comes_from_the_headline_with_the_specific_cases_first():
    stage = export_news.court_stage
    assert stage("Auftakt der Hauptverhandlung wegen Totschlags") == "auftakt"
    assert stage("Auftakt der Hauptverhandlung wegen Totschlags nach Aufhebung durch den Bundesgerichtshof") == "auftakt"
    assert stage("Termin zur Urteilsverkündung im Verfahren wegen Raubes") == "urteilstermin"
    assert stage("Urteil im Verfahren wegen Mordes") == "urteil"
    assert stage("Entscheidung des Bundesgerichtshofs hinsichtlich der Verurteilung vom 08.04.2024") == "sonstiges"
    assert stage("Terminsverschiebung für Auftakt der Hauptverhandlung wegen Totschlags") == "terminaenderung"
    assert stage("Die Hauptverhandlungstermine in dem Verfahren wurden aufgehoben!") == "terminaenderung"
    assert stage("Rechtskraft im Verfahren wegen Geiselnahme") == "rechtskraft"
    assert stage("Wechsel an der Spitze des Landgerichts") == "sonstiges"


LG_TEXT = ("Landgericht Bremen \n - Pressestelle - \n \nPressemitteilung Nr. 63/2026 \nvom 11. September 2026 \n \n"
           "Auftakt der Hauptverhandlung  \nwegen Totschlags \n \n21 Ks 250 Js 19710/26 - Beginn: Freitag \n \nZeile eins \nZeile zwei")


def court_fixture():
    _, geo = frames([(19126.0, "Tötungsdelikt in der Neustadt", "Neustadt", "Toetungsdelikt", True),
                     (20026.0, "Etwas ganz anderes", "Mitte", "Raub", True)])
    lg = [{"pm_id": "lg-2026-63", "court": "Landgericht Bremen", "title": "Auftakt im Verfahren wegen Totschlags u.a_",
           "stage": "auftakt", "category": "Verkehrsunfall", "publish_date": "2026-09-11",
           "pdf_url": "https://example.org/63.pdf", "text": LG_TEXT},
          {"pm_id": "lg-2024-1", "court": "Landgericht Bremen", "title": "Alt", "stage": "urteil",
           "category": "Raub", "publish_date": "2024-01-02", "pdf_url": "https://example.org/1.pdf", "text": "x"}]
    ag = [{"weekday": "Montag", "date": "12.10.2026", "time": "10:00", "room_info": "Saal 251", "offense": "Betrug",
           "az": "94 Ds 926 Js 62677/22", "narrative": "Früh.", "category": "Sonstiges"},
          {"weekday": "Montag", "date": "12.10.2026", "time": "13:00", "room_info": "Saal 551",
           "offense": "Körperverletzung", "az": "84 Ds 682 Js 95097/24", "category": "Koerperverletzung",
           "narrative": "Dem Angeklagten wird ... gegeben haben soll. Verantwortlich Richter am Amtsgericht N. N. "
                        "Pressesprecher des Amtsgerichts Bremen Ostertorstraße 25"}]
    matches = [{"matched_pm_ids": [19126], "releases": [{"pm_id": "lg-2026-63"}]}]
    return lg, ag, matches, geo


def test_court_list_holds_releases_and_hearings_newest_first():
    lg, ag, matches, geo = court_fixture()
    cases = export_news.build_court(lg, ag, matches, geo, export_news.key_maker([]))
    assert [c["id"] for c in cases] == ["ag-84-ds-682-js-95097-24-2026-10-12", "ag-94-ds-926-js-62677-22-2026-10-12",
                                        "lg-2026-63", "lg-2024-1"]            # same day: by time, not by file number
    assert cases[0] == {
        "id": "ag-84-ds-682-js-95097-24-2026-10-12", "court": "Amtsgericht Bremen", "date": "2026-10-12",
        "time": "13:00", "title": "Verhandlung: Körperverletzung", "stage": "termin", "url": export_news.AG_URL,
        "text": "Montag, 12.10.2026, 13:00 Uhr – Saal 551\nAktenzeichen: 84 Ds 682 Js 95097/24\n\n"
                "Dem Angeklagten wird ... gegeben haben soll.",                # the press office's imprint is cut off
        "reports": []}
    assert cases[2] == {
        "id": "lg-2026-63", "court": "Landgericht Bremen", "date": "2026-09-11", "time": "",
        "title": "Auftakt der Hauptverhandlung wegen Totschlags",              # from the text, not the file name
        "stage": "auftakt", "url": "https://example.org/63.pdf", "text": export_news.reflow(LG_TEXT),
        "reports": [{"k": "19126", "title": "Tötungsdelikt in der Neustadt", "date": "2026-10-06"}]}   # only the matched one
    assert cases[3]["title"] == "Alt" and cases[3]["reports"] == []           # no headline in the text: file name
    index = export_news.court_index(cases)
    assert index[2] == {"id": "lg-2026-63", "d": "2026-09-11", "t": "Auftakt der Hauptverhandlung wegen Totschlags",
                        "court": "Landgericht Bremen", "stage": "auftakt"}


def test_court_entry_the_court_no_longer_lists_is_kept():
    lg, ag, matches, geo = court_fixture()
    key = export_news.key_maker([])
    yesterday = export_news.build_court(lg, ag, matches, geo, key)
    today = export_news.build_court([], [], [], geo, key, kept=yesterday)       # court site lists nothing today
    assert [c["id"] for c in today] == [c["id"] for c in yesterday]
    refreshed = export_news.build_court([dict(lg[1], text="neuer Text")], [], [], geo, key, kept=yesterday)
    assert next(c for c in refreshed if c["id"] == "lg-2024-1")["text"] == "neuer Text"


# ---- the files ---------------------------------------------------------------

class Folder:
    """A throwaway data/processed + assets pair with one police release on the map."""

    def __init__(self, with_court_data):
        self.dir = Path(tempfile.mkdtemp(prefix="news-test-"))
        atexit.register(shutil.rmtree, self.dir, ignore_errors=True)
        export_news.PROCESSED_DIR, export_news.ASSETS_DIR = self.dir / "processed", self.dir / "assets"
        export_news.PROCESSED_DIR.mkdir()
        export_news.ASSETS_DIR.mkdir()
        releases = [release("002825", "Raub in Walle", text="Zeile eins Zeile zwei"), release("000005", "Warnung")]
        (export_news.PROCESSED_DIR / "releases.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in releases), encoding="utf-8")
        incidents, geo = frames([(2825.0, "Raub in Walle", "Walle", "Raub", True), (5.0, "Warnung", None, "Sonstiges", False)])
        incidents.to_csv(export_news.PROCESSED_DIR / "incidents.csv", index=False)
        geo.to_csv(export_news.PROCESSED_DIR / "incidents_geo.csv", index=False)
        if with_court_data:
            lg, ag, matches, _ = court_fixture()
            for name, records in (("court_releases.jsonl", lg), ("amtsgericht_log.jsonl", ag)):
                (export_news.PROCESSED_DIR / name).write_text(
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")

    def run(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            export_news.main()
        self.log = out.getvalue()
        return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in export_news.ASSETS_DIR.glob("*.json")}


def test_all_three_files_are_written_and_agree_with_each_other():
    files = Folder(with_court_data=True).run()
    assert set(files) == {"news_index.json", "police_other.json", "court_cases.json"}
    assert [e["k"] for e in files["news_index.json"]["police"]] == ["2825", "5"]   # a text with U+2028 was read back whole
    assert set(files["police_other.json"]) == {"5"}
    assert [c["id"] for c in files["news_index.json"]["court"]] == [c["id"] for c in files["court_cases.json"]]
    assert len(files["court_cases.json"]) == 4


def test_pc_without_court_data_keeps_the_court_list_it_was_given():
    shipped = Folder(with_court_data=True).run()["court_cases.json"]
    folder = Folder(with_court_data=False)                                          # e.g. the standalone package
    (export_news.ASSETS_DIR / "court_cases.json").write_text(json.dumps(shipped), encoding="utf-8")
    files = folder.run()
    assert files["court_cases.json"] == shipped
    assert [c["id"] for c in files["news_index.json"]["court"]] == [c["id"] for c in shipped]
    assert set(files["police_other.json"]) == {"5"}


def test_court_page_that_lists_nothing_any_more_does_not_empty_the_court_list():
    folder = Folder(with_court_data=True)
    before = folder.run()["court_cases.json"]
    (export_news.PROCESSED_DIR / "court_releases.jsonl").write_text("", encoding="utf-8")
    assert folder.run()["court_cases.json"] == before


def test_export_refuses_inputs_that_are_older_than_the_archive():
    folder = Folder(with_court_data=False)
    with (export_news.PROCESSED_DIR / "releases.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(release("009926", "Neu, aber build_dataset lief nicht")) + "\n")
    try:
        folder.run()
    except export_news.StaleInput:
        pass
    else:
        raise AssertionError("a release missing from incidents.csv must stop the export")
    assert list(export_news.ASSETS_DIR.glob("*")) == []            # nothing written, not even a temp file


def test_map_export_writes_nothing_when_the_lists_cannot_be_built():
    import export_map_data
    folder = Folder(with_court_data=True)
    geo = pd.read_csv(export_news.PROCESSED_DIR / "incidents_geo.csv")
    geo = geo.assign(lat=53.08, lon=8.81, precision="district", ort_raw="Bremen-Walle", text="Text", source_url="")
    geo.to_csv(export_news.PROCESSED_DIR / "incidents_geo.csv", index=False)
    export_map_data.ROOT, export_map_data.PROCESSED_DIR = folder.dir, export_news.PROCESSED_DIR
    with contextlib.redirect_stdout(io.StringIO()):
        export_map_data.main()
    good = {p.name: p.read_bytes() for p in export_news.ASSETS_DIR.glob("*")}
    assert {"map_data.json", "news_index.json", "police_other.json", "court_cases.json"} <= set(good)
    record = json.loads(good["map_data.json"])[0]
    assert record["k"] == "2825" and record["pub"] == "2026-10-07" and record["title"] == "Raub in Walle"

    with (export_news.PROCESSED_DIR / "releases.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(release("009926", "Neu, aber build_dataset lief nicht")) + "\n")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            export_map_data.main()
    except export_news.StaleInput:
        pass
    else:
        raise AssertionError("the export must fail as a whole")
    assert {p.name: p.read_bytes() for p in export_news.ASSETS_DIR.glob("*")} == good


def test_written_files_are_strict_json():
    folder = Folder(with_court_data=False)
    try:
        export_news.write_all({export_news.ASSETS_DIR / "x.json": [{"title": float("nan")}]})
    except ValueError:
        pass
    else:
        raise AssertionError("NaN is not JSON a browser accepts")


def test_cut_off_line_in_the_hearing_log_is_skipped_not_fatal():
    folder = Folder(with_court_data=True)
    with (export_news.PROCESSED_DIR / "amtsgericht_log.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"weekday": "Dienstag", "date": "13.10.20')
    files = folder.run()
    assert len(files["court_cases.json"]) == 4 and "not readable" in folder.log


def test_dates():
    assert export_news.iso_date("07.10.2026") == "2026-10-07"
    assert export_news.iso_date("7.1.2026") == "2026-01-07"
    assert export_news.iso_date("11.02.206") is None
    assert export_news.iso_date(None) is None
    assert export_news.iso_date(float("nan")) is None
    assert export_news.iso_date("31.13.2026") is None
    assert export_news.iso_date("30.02.2026") is None


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
