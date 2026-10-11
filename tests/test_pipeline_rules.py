"""
Small rules of the pipeline that are easy to break without noticing: which
Stadtteil a release belongs to, which day an incident is dated, when the
daily update counts as due, and when an upload counts as done.

Run:  python tests/test_pipeline_rules.py     (or: pytest tests)
"""
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import build_dataset
import districts
import repo_guard
import update_gate


def stadtteile(ort):
    return districts.stadtteile_of(ort)


# ---- where ---------------------------------------------------------------------

def test_usual_spelling():
    assert stadtteile("Bremen-Vegesack, OT Fähr-Lobbendorf, Kirchhofstraße") == ["Vegesack"]
    assert stadtteile("Bremen-Neustadt/Bremen-Mitte, OT Neustadt/Altstadt, Stephanibrücke") == ["Neustadt", "Mitte"]
    assert stadtteile("Bremen-Mitte OT Bahnhofsvorstadt") == ["Mitte"]


def test_other_spellings_the_press_office_uses():
    assert stadtteile("Bremen Findorff, Münchener Straße/Kastanienstraße") == ["Findorff"]
    assert stadtteile("Bremen – Gröpelingen, Seewenjestraße") == ["Gröpelingen"]
    assert stadtteile("Bremen - Horn-Lehe, OT Lehesterdeich, Fultonstraße") == ["Horn-Lehe"]
    assert stadtteile("Bremen, Vegesack, OT Grohn") == ["Vegesack"]
    assert stadtteile("Bemen-Mitte, OT Bahnhofsvorstadt") == ["Mitte"]
    assert stadtteile("Bremen-Vahr/Bundesautobahn 27") == ["Vahr"]
    assert stadtteile("Bremen Horn-Lehe") == ["Horn-Lehe"]


def test_area_prefixes_ortsteile_and_compounds():
    assert stadtteile("Bremen-West, Gröpelingen, OT Lindenhof, Stapelfeldtstraße") == ["Gröpelingen"]
    assert stadtteile("Bremen-West, Findorff, Gröpelingen") == ["Findorff", "Gröpelingen"]
    assert stadtteile("Bremen-Nord, OT Fähr-Lobbendorf, Fritz-Wildung-Straße") == ["Vegesack"]
    assert stadtteile("Bremen-Nord, Blumenthal, OT Blumenthal und Vegesack, OT Fähr-Lobbendorf") == ["Blumenthal", "Vegesack"]
    assert stadtteile("Bremen, Burgdamm, Helsingborger Straße") == ["Burglesum"]
    assert stadtteile("Bremen, Mitte-Östliche Vorstadt") == ["Mitte", "Östliche Vorstadt"]
    assert stadtteile("Bremen-Obervieland-Industriehäfen") == ["Obervieland", "Häfen"]
    assert stadtteile("Bremen-Steintor") == ["Östliche Vorstadt"]


def test_a_street_or_a_stadium_is_not_taken_for_a_stadtteil():
    assert stadtteile("Bremen, Mittelweg") == []
    assert stadtteile("Bremen, Stadtgebiet") == []
    assert stadtteile("Bremen Vegesacker Straße") == []
    assert stadtteile("Bremen, Waller Heerstraße") == []
    assert stadtteile("Bremen, Weserstadion") == []                 # match announcements are no incident in a Stadtteil
    assert stadtteile("Bremen-Weserstadion") == []
    assert stadtteile("Bremerhaven-Lehe, Hafenstraße") == []
    assert stadtteile("Hamburg") == []
    assert stadtteile(None) == []


def test_every_ortsteil_belongs_to_a_known_stadtteil():
    assert set(districts.ORTSTEILE) <= districts.KNOWN_STADTTEILE
    assert set(districts.LANDMARKS.values()) <= districts.KNOWN_STADTTEILE


def test_bremerhaven_means_the_city_not_a_street_named_after_it():
    assert districts.is_bremerhaven("Bremerhaven-Geestemünde, Bachstraße")
    assert districts.is_bremerhaven("Bremerhaven")
    assert not districts.is_bremerhaven("Bremen-Walle, OT Westend, Bremerhavener Straße")
    assert not districts.is_bremerhaven("Bremen-Burglesum, OT Burgdamm, Bremerhavener Heerstraße")


# ---- when ----------------------------------------------------------------------

def test_incident_date_is_the_day_it_happened():
    assert build_dataset.parse_incident_date("06.10.26, 13.30 Uhr", "07.10.2026") == date(2026, 10, 6)
    assert build_dataset.parse_incident_date("07.10.2026", "07.10.2026") == date(2026, 10, 7)


TODAY = date(2026, 10, 11)


def test_nothing_is_dated_after_its_own_publication():
    # an announcement (football match, seminar)
    assert build_dataset.parse_incident_date("28.9.26, 18 bis 20 Uhr", "15.09.2026", TODAY) == date(2026, 9, 15)
    assert build_dataset.parse_incident_date("08.10.26", "07.10.2026", TODAY) == date(2026, 10, 7)   # even by one day


def test_nothing_is_dated_in_the_future():
    # no publication day known: a "Zeit" that hasn't come yet is an announcement, not an incident
    assert build_dataset.parse_incident_date("24.10.26, 15.30 Uhr", None, TODAY) is None
    assert build_dataset.parse_incident_date("10.10.26", None, TODAY) == date(2026, 10, 10)
    # publication day typed into next year: not believed
    assert build_dataset.parse_incident_date("10.10.26", "11.10.2027", TODAY) == date(2026, 10, 10)
    assert build_dataset.parse_incident_date(None, "11.10.2027", TODAY) is None


def test_mistyped_publication_month_does_not_misdate_the_incident():
    # 616/26 was typed "14.08.2026"; its neighbours and its page say September, and so does its "Zeit"
    page = "https://www.polizei.bremen.de/news/pressestelle/pressemeldungen-ab-11092026-69054"
    releases = [{"pm_id": pm, "title": t, "publish_date": pub, "zeit_raw": zeit, "ort_raw": "Bremen-Oberneuland",
                 "text": "x", "source_url": page}
                for pm, t, pub, zeit in (("061526", "a", "14.09.2026", "13.9.26"),
                                         ("061626", "Autofahrer fährt gegen Anhänger", "14.08.2026", "14.9.26, 9.50 Uhr"),
                                         ("061726", "c", "15.09.2026", "14.9.26"))]
    rows = build_dataset.build_incidents(releases, today=TODAY)
    row = rows[rows["title"] == "Autofahrer fährt gegen Anhänger"].iloc[0]
    assert row["pub"] == "2026-09-14" and row["incident_date"] == date(2026, 9, 14)


def test_old_case_and_unreadable_time_fall_back_to_the_publication_day():
    assert build_dataset.parse_incident_date("November 2022", "07.10.2026") == date(2026, 10, 7)
    assert build_dataset.parse_incident_date("31.10.2024", "28.01.2025") == date(2024, 10, 31)   # within 90 days
    assert build_dataset.parse_incident_date("01.03.2024", "28.01.2025") == date(2025, 1, 28)    # a cold case


# ---- is an update due? ------------------------------------------------------------

def gate(done_hours_ago=None, attempt_hours_ago=None):
    folder = Path(tempfile.mkdtemp(prefix="gate-test-"))
    update_gate.STAMP_PATH, update_gate.ATTEMPT_PATH = folder / "done.txt", folder / "attempt.txt"
    for path, hours in ((update_gate.STAMP_PATH, done_hours_ago), (update_gate.ATTEMPT_PATH, attempt_hours_ago)):
        if hours is not None:
            path.write_text((datetime.now() - timedelta(hours=hours)).isoformat(), encoding="utf-8")
    old_argv, sys.argv = sys.argv, ["update_gate.py"]
    try:
        update_gate.main()
    except SystemExit as e:
        return "update" if e.code == 0 else "skip"
    finally:
        sys.argv = old_argv


def test_update_is_due_once_a_day():
    assert gate() == "update"                                   # never ran
    assert gate(done_hours_ago=3) == "skip"
    assert gate(done_hours_ago=30) == "update"


def test_failed_update_is_retried_but_not_on_every_start():
    assert gate(done_hours_ago=30, attempt_hours_ago=0.5) == "skip"     # tried half an hour ago, didn't complete
    assert gate(done_hours_ago=30, attempt_hours_ago=3) == "update"
    assert gate(attempt_hours_ago=0.5) == "skip"
    assert gate(done_hours_ago=3, attempt_hours_ago=30) == "skip"


# ---- did the upload really happen? ---------------------------------------------------

class Clone:
    """A repository with its own stand-in for GitHub."""

    def __init__(self):
        base = Path(tempfile.mkdtemp(prefix="push-test-"))
        self.dir = base / "work"
        self.git_in(base, "init", "-q", "--bare", "remote.git")
        self.git_in(base, "clone", "-q", "remote.git", "work")
        repo_guard.ROOT = self.dir
        (self.dir / "data").mkdir()
        (self.dir / "docs").mkdir()
        (self.dir / "docs" / "map_data.json").write_text("[]", encoding="utf-8")
        self.commit("init")
        self.git("push", "-q", "-u", "origin", "HEAD")

    @staticmethod
    def git_in(folder, *args):
        return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, check=True).stdout

    def git(self, *args):
        return self.git_in(self.dir, "-c", "user.name=t", "-c", "user.email=t@example.com", *args)

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-q", "-m", message)


def test_upload_only_counts_when_nothing_is_left_behind():
    clone = Clone()
    assert repo_guard.left_behind() is None
    (clone.dir / "docs" / "map_data.json").write_text("[1]", encoding="utf-8")        # changed, never committed
    assert "nicht eingetragen" in repo_guard.left_behind()
    clone.commit("data")                                                               # committed, never pushed
    assert "nicht zu GitHub" in repo_guard.left_behind()
    clone.git("push", "-q")
    assert repo_guard.left_behind() is None
    (clone.dir / "notes.txt").write_text("not something the update publishes", encoding="utf-8")
    assert repo_guard.left_behind() is None


def test_file_held_back_for_its_size_is_not_a_failed_upload():
    clone = Clone()
    (clone.dir / "data" / "huge.bin").write_bytes(b"x" * 10)          # the guard took it out of the commit
    assert "nicht eingetragen" in repo_guard.left_behind()
    assert repo_guard.left_behind(held_back=["data/huge.bin"]) is None


def test_push_that_had_nothing_to_send_is_not_reported_as_success():
    status = repo_guard.pushed(0, {}, waiting="Änderungen konnten nicht eingetragen werden")
    assert status["last_push_ok"] is False and status["push_problem"]
    status = repo_guard.pushed(0, status, waiting=None)
    assert status["last_push_ok"] is True and status["push_problem"] is None and status["push_failing_since"] is None


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
