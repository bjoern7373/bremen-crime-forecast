"""
Checks that the scraper never loses a press release it once had, whatever the
police site, the Wayback Machine or the network do. No real network access.

Run:  python tests/test_scrape_archive.py     (or: pytest tests)
"""
import atexit
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import scrape

BASE = scrape.CHUNK_BASE
OLD = BASE + "pressemeldungen-ab-01012026-100"
MID = BASE + "pressemeldungen-ab-01022026-200"
NEW = BASE + "pressemeldungen-ab-01032026-300"
NEWER = BASE + "pressemeldungen-ab-01042026-400"
NEWEST = BASE + "pressemeldungen-ab-01052026-500"

LONG = ("Einsatzkräfte der Polizei Bremen nahmen am Montag in der Neustadt zwei Männer fest. "
        "Sie sollen zuvor einen 34-Jährigen angegriffen und ihm das Mobiltelefon abgenommen haben. ") * 3


def page(*entries, complete=True):
    """entries: (pm_id, title[, text[, date]])"""
    blocks = []
    for e in entries:
        pm_id, title = e[0], e[1]
        text = e[2] if len(e) > 2 else f"Text of {title}"
        date = e[3] if len(e) > 3 else "01.03.2026"
        body = f"<p>{text}</p>" if text else ""
        blocks.append(
            f'<div class="entry-wrapper-1col" id="{pm_id}"><h2>{title}</h2>'
            f"<p>({date})</p><p>Ort: Bremen-Mitte<br>Zeit: {date}, 10 Uhr</p>{body}</div>"
        )
    return "<html><body>" + "".join(blocks) + ("</body></html>" if complete else "")


def not_found(url):
    resp = requests.Response()
    resp.status_code = 404
    raise requests.exceptions.HTTPError("404", response=resp)


def offline(url, *a, **k):
    raise requests.exceptions.ConnectionError("no network")


class Env:
    """A throwaway project folder with every network call replaced."""

    def __init__(self, chunks=(OLD, MID, NEW)):
        self.dir = Path(tempfile.mkdtemp(prefix="scrape-test-"))
        atexit.register(shutil.rmtree, self.dir, ignore_errors=True)
        scrape.RAW_DIR = self.dir / "raw"
        scrape.PROCESSED_DIR = self.dir / "processed"
        scrape.ARCHIVE_PATH = self.dir / "archive" / "releases.jsonl"
        scrape.KNOWN_CHUNKS_PATH = self.dir / "known_chunks.json"
        scrape.CHUNK_URLS = list(chunks)
        scrape.RAW_DIR.mkdir(parents=True)
        self.live = {}          # url -> html, or a callable(url) that raises
        self.asked = []         # live download attempts, in order
        self.wayback = lambda url, target: None
        self.wayback_calls = []
        self.discovery_html = None
        self.log = ""
        scrape._fetch_live = self._fetch_live
        scrape._wayback_fallback = self._wayback
        scrape.requests.get = self._discovery_get

    def _fetch_live(self, url):
        self.asked.append(url)
        source = self.live.get(url, not_found)
        return source(url) if callable(source) else source

    def _wayback(self, url, target):
        self.wayback_calls.append(url)
        return self.wayback(url, target)

    def _discovery_get(self, url, **kwargs):
        if self.discovery_html is None:
            raise requests.exceptions.ConnectionError("no network")
        resp = requests.Response()
        resp.status_code = 200
        resp._content = self.discovery_html.encode("utf-8")
        return resp

    def discover(self, *urls):
        self.discovery_html = "".join(f'<a href="/news/pressestelle/{scrape._slug(u)}#0001">x</a>' for u in urls)

    def store(self, url, html):
        scrape._cache_path(url).write_text(html, encoding="utf-8")

    def run(self, mode="update"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                archive, self.up_to_date = scrape.run(mode, polite_delay=0)
            finally:
                self.log = out.getvalue()
        return archive

    def main(self, *argv):
        """Run the command line entry point; returns its exit code."""
        old_argv, sys.argv = sys.argv, ["scrape.py", *argv]
        old_sleep, scrape.time.sleep = scrape.time.sleep, lambda s: None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                scrape.main()
        except SystemExit as e:
            return e.code
        finally:
            sys.argv, scrape.time.sleep = old_argv, old_sleep
        return None

    def records(self):
        return [json.loads(line) for line in
                scrape.ARCHIVE_PATH.read_text(encoding="utf-8").split("\n") if line]

    def titles(self):
        return sorted(r["title"] for r in self.records())

    def snapshots(self, url=NEW):
        return sorted(p.name for p in scrape.RAW_DIR.glob(f"{scrape._slug(url)}@*.html"))


# ---- nothing we once had gets lost -----------------------------------------

def test_release_removed_from_a_page_is_kept():
    env = Env()
    env.store(NEW, page(("1", "A"), ("2", "B")))
    env.live[NEW] = page(("1", "A"))                    # B vanished, nothing else changed
    env.run()
    assert env.titles() == ["A", "B"]
    assert len(env.snapshots()) == 1
    env.run()                                           # and it stays that way, without more snapshots
    assert env.titles() == ["A", "B"]
    assert len(env.snapshots()) == 1


def test_release_removed_while_another_is_added():
    env = Env()
    env.store(NEW, page(("1", "A"), ("2", "B")))
    env.live[NEW] = page(("1", "A"), ("3", "C"))
    env.run()
    assert env.titles() == ["A", "B", "C"]


def test_network_failure_keeps_working_from_our_copy():
    env = Env()
    env.store(NEW, page(("1", "A")))
    env.live[NEW] = offline
    env.live[MID] = offline
    env.run()
    assert env.titles() == ["A"]
    assert "A" in scrape._cache_path(NEW).read_text(encoding="utf-8")


def test_page_without_entries_does_not_replace_our_copy():
    env = Env()
    env.store(NEW, page(("1", "A")))
    env.live[NEW] = "<html><body>Wartungsarbeiten</body></html>"
    env.run()
    assert env.titles() == ["A"]
    assert env.up_to_date is False
    assert "Wartungsarbeiten" not in scrape._cache_path(NEW).read_text(encoding="utf-8")


def test_cut_off_download_does_not_replace_our_copy():
    env = Env()
    env.store(NEW, page(("1", "A", LONG), ("2", "B", LONG)))
    env.live[NEW] = page(("1", "A", LONG), ("2", "B", LONG[:40]), complete=False)
    archive = env.run()
    assert archive["2"]["text"] == LONG.strip()
    assert env.up_to_date is False
    assert env.snapshots() == []                        # our copy was not touched at all


def test_entry_shortened_in_place_keeps_the_full_text():
    env = Env()
    env.store(NEW, page(("1", "A", LONG)))
    env.live[NEW] = page(("1", "A", "Die Fahndung hat sich erledigt."))
    archive = env.run()
    assert archive["1"]["text"] == LONG.strip()
    assert len(env.snapshots()) == 1                    # the full version is still on disk as well
    archive = env.run("stored")                         # ... and a plain re-parse doesn't change that
    assert archive["1"]["text"] == LONG.strip()


def test_correction_by_the_press_office_is_taken_over():
    env = Env()
    env.store(NEW, page(("1", "A", LONG + "Der Täter trug eine rote Jacke.")))
    env.live[NEW] = page(("1", "A (Korrektur)", LONG + "Der Täter trug eine blaue Jacke."))
    archive = env.run()
    assert len(archive) == 1
    assert archive["1"]["title"] == "A (Korrektur)" and "blaue" in archive["1"]["text"]


def test_empty_text_never_replaces_a_stored_text():
    env = Env()
    env.store(NEW, page(("1", "A", "the full text")))
    env.run("stored")
    env.live[NEW] = page(("1", "A", ""))
    archive = env.run()
    assert archive["1"]["text"] == "the full text"


def test_deleted_page_we_hold_never_needs_the_wayback_machine():
    env = Env()
    for url, title in ((OLD, "A"), (MID, "B"), (NEW, "C")):
        env.store(url, page((title, title)))
    env.live[NEW] = page(("C", "C"), ("D", "D"))        # OLD and MID answer 404
    env.run()
    assert env.titles() == ["A", "B", "C", "D"]
    assert env.wayback_calls == []


def test_archive_survives_losing_every_stored_page():
    env = Env()
    env.store(NEW, page(("1", "A"), ("2", "B")))
    env.run("stored")
    scrape._cache_path(NEW).unlink()
    for url in (OLD, MID, NEW):
        env.live[url] = offline
    env.run()
    assert env.titles() == ["A", "B"]


# ---- ids are typed by hand: one id, two releases ---------------------------

def test_reused_id_on_the_same_page_keeps_both_releases():
    env = Env()
    env.store(NEW, page(("064326", "Jugendliche verletzen Obdachlosen", LONG)))
    env.live[NEW] = page(("064326", "Jugendliche verletzen Obdachlosen", LONG),
                         ("064326", "Raub in Walle", "Ein ganz anderer Vorfall, versehentlich mit derselben Nummer."))
    env.run()
    assert env.titles() == ["Jugendliche verletzen Obdachlosen", "Raub in Walle"]
    env.run()                                           # stable over further runs and re-loads
    env.run("stored")
    assert env.titles() == ["Jugendliche verletzen Obdachlosen", "Raub in Walle"]


def test_mistyped_id_does_not_replace_an_old_release_from_another_page():
    env = Env()
    env.store(OLD, page(("064625", "Brände im Bremer Osten", LONG, "01.10.2025")))
    env.store(NEW, page(("1", "A")))
    env.run("stored")
    env.live[NEW] = page(("1", "A"), ("064625", "Einbruch in Kiosk", "Unbekannte brachen in einen Kiosk ein."))
    env.run()
    assert env.titles() == ["A", "Brände im Bremer Osten", "Einbruch in Kiosk"]
    scrape._cache_path(OLD).unlink()                    # even when only the archive still has the old one
    env.run()
    assert env.titles() == ["A", "Brände im Bremer Osten", "Einbruch in Kiosk"]


def test_releases_without_an_id_do_not_overwrite_each_other():
    env = Env()
    env.store(NEW, page(("", "first"), ("", "second"),
                        ("", "Einbrecher gestellt", "In Walle.", "02.03.2026"),
                        ("", "Einbrecher gestellt", "Diesmal in Huchting, ein ganz anderer Fall.", "09.03.2026")))
    env.run("stored")
    assert env.titles() == ["Einbrecher gestellt", "Einbrecher gestellt", "first", "second"]


def test_id_added_later_does_not_count_the_release_twice():
    env = Env()
    env.store(NEW, page(("", "Kupferdiebe gefasst"), ("67200", "Raub in Walle")))
    env.run("stored")
    env.live[NEW] = page(("012726", "Kupferdiebe gefasst"), ("012826", "Raub in Walle"))
    env.run()
    assert sorted((r["pm_id"], r["title"]) for r in env.records()) == [
        ("012726", "Kupferdiebe gefasst"), ("012826", "Raub in Walle")]


# ---- which pages get downloaded --------------------------------------------

def test_only_the_newest_pages_are_downloaded():
    env = Env()
    for url, title in ((OLD, "A"), (MID, "B"), (NEW, "C")):
        env.store(url, page((title, title)))
    env.run()
    assert env.asked == [MID, NEW]


def test_new_archive_page_is_discovered_and_remembered():
    env = Env()
    env.store(NEW, page(("1", "A")))
    env.discover(NEWER)
    env.live[NEWER] = page(("9", "Z"))
    env.run()
    assert env.titles() == ["A", "Z"]
    assert NEWER in scrape.load_known_chunks()
    env.discovery_html = None                           # overview unreachable next time
    env.live[NEWER] = page(("9", "Z"), ("10", "Y"))
    env.run()
    assert env.titles() == ["A", "Y", "Z"]


def test_two_new_pages_since_the_last_run_still_refresh_the_page_that_was_newest_then():
    env = Env()
    env.store(MID, page(("1", "A")))
    env.store(NEW, page(("2", "B")))                    # newest page at the last run
    env.discover(NEWER, NEWEST)                         # the PC was off for weeks
    env.live[NEW] = page(("2", "B"), ("3", "late addition"))
    env.live[NEWER] = page(("4", "C"))
    env.live[NEWEST] = page(("5", "D"))
    env.run()
    assert env.titles() == ["A", "B", "C", "D", "late addition"]


def test_stored_page_is_known_even_without_a_seed_or_discovery_entry():
    env = Env(chunks=(NEW,))
    env.store(NEW, page(("1", "A")))
    env.store(NEWER, page(("2", "B")))                  # e.g. shipped in a package
    env.live[NEWER] = page(("2", "B"), ("3", "C"))
    env.run()
    assert env.titles() == ["A", "B", "C"]


def test_wayback_machine_fills_in_a_deleted_page_we_have_no_copy_of():
    env = Env()
    env.store(NEW, page(("1", "A")))
    env.wayback = lambda url, target: page(("7", "from the Wayback Machine")) if url == MID else None
    env.run()
    assert env.titles() == ["A", "from the Wayback Machine"]
    assert scrape._cache_path(MID).exists()             # now our own copy


def test_wayback_machine_failing_does_not_stop_the_run():
    env = Env()
    env.store(NEW, page(("1", "A")))

    def timeout(url, target):
        raise requests.exceptions.ReadTimeout("slow")
    env.wayback = timeout
    env.live[NEW] = page(("1", "A"), ("2", "B"))
    env.run()
    assert env.titles() == ["A", "B"]
    assert env.up_to_date is True


# ---- several stored versions of one page -----------------------------------

def test_snapshots_of_the_same_day_are_read_in_the_order_they_were_made():
    env = Env()
    env.store(NEW, page(("1", "A"), ("2", "B", "B version 1"), ("3", "C")))
    env.live[NEW] = page(("1", "A"), ("2", "B", "B version 2"))      # C removed, B corrected
    env.run()
    env.live[NEW] = page(("1", "A"))                                  # later the same day: B removed
    archive = env.run()
    assert len(env.snapshots()) == 2
    assert archive["2"]["text"] == "B version 2"
    assert env.run("stored")["2"]["text"] == "B version 2"


# ---- the archive file itself -------------------------------------------------

def test_first_run_adopts_the_previous_export():
    env = Env()
    scrape.PROCESSED_DIR.mkdir(parents=True)
    old_record = {"pm_id": "77", "source_url": OLD, "title": "from before", "publish_date": None,
                  "ort_raw": None, "zeit_raw": None, "text": "x"}
    (scrape.PROCESSED_DIR / "releases.jsonl").write_text(json.dumps(old_record) + "\n", encoding="utf-8")
    env.store(NEW, page(("1", "A")))
    env.run("stored")
    assert env.titles() == ["A", "from before"]


def test_damaged_archive_stops_the_run_instead_of_being_overwritten():
    env = Env()
    env.store(NEW, page(("1", "A")))
    env.run("stored")
    before = scrape.ARCHIVE_PATH.read_text(encoding="utf-8")
    scrape.ARCHIVE_PATH.write_text(before + '{"pm_id": "2", "title": "tr', encoding="utf-8")
    try:
        env.run("stored")
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("a damaged archive must stop the run")
    assert scrape.ARCHIVE_PATH.read_text(encoding="utf-8").startswith(before)


def test_emptied_or_cut_short_archive_file_loses_nothing():
    for damage in (lambda text: "", lambda text: text[:text.index("\n") + 1]):
        env = Env()
        env.store(NEW, page(("1", "A"), ("2", "B"), ("3", "C")))
        env.run("stored")
        env.store(NEW, page(("1", "A")))                # B and C now exist only in the archive
        scrape.ARCHIVE_PATH.write_text(damage(scrape.ARCHIVE_PATH.read_text(encoding="utf-8")),
                                       encoding="utf-8")
        env.run("stored")
        assert env.titles() == ["A", "B", "C"]
        assert "differ" in env.log


def test_unusual_line_separator_characters_in_a_text_do_not_break_the_archive():
    for char in (" ", " ", "\x85"):
        env = Env()
        env.store(NEW, page(("1", "A", f"first line{char}second line")))
        env.run("stored")
        env.live[NEW] = page(("1", "A", f"first line{char}second line"), ("2", "B"))
        env.run()                                       # reads the archive back
        assert env.titles() == ["A", "B"]


def test_copy_for_the_next_pipeline_step_matches_the_archive():
    env = Env()
    env.store(NEW, page(("1", "A"), ("2", "B")))
    env.run("stored")
    assert ((scrape.PROCESSED_DIR / "releases.jsonl").read_bytes() == scrape.ARCHIVE_PATH.read_bytes())


# ---- what the daily job sees: the command line -------------------------------

def test_exit_code_tells_the_daily_job_whether_the_newest_page_was_fetched():
    env = Env()
    env.store(NEW, page(("1", "A")))
    env.live[NEW] = page(("1", "A"), ("2", "B"))
    assert env.main("--update") == 0
    assert env.titles() == ["A", "B"]                   # --update really downloads
    env.live[NEW] = offline
    assert env.main("--update") == scrape.EXIT_NOT_UP_TO_DATE
    env.live[NEW] = "<html><body>Wartungsarbeiten</body></html>"
    assert env.main("--update") == scrape.EXIT_NOT_UP_TO_DATE
    del env.live[NEW]                                   # 404 although it is the newest page
    assert env.main("--update") == scrape.EXIT_NOT_UP_TO_DATE
    assert env.main() == 0                              # re-parsing our copies is never "out of date"
    assert scrape.EXIT_NOT_UP_TO_DATE not in (0, 1, 2)  # 1 = crash, 2 = wrong arguments


def test_date_line_formats():
    parse = scrape._parse_pubdate_line
    assert parse("(07.10.2026)") == "07.10.2026"
    assert parse("07.10.2026") == "07.10.2026"
    assert parse("(06.02.26)") == "06.02.2026"
    assert parse("(11.02.206)") is None
    assert parse("Ort: Bremen") is None


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
