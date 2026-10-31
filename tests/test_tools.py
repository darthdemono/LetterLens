import datetime
import json
import os

import pytest

import tools


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(tools, "today", lambda: datetime.date(2026, 10, 31))


def test_portals_are_loaded_and_have_links():
    data = tools.load_portals()
    assert len(data) == 195
    hungary = data["Hungary"]
    assert {"https://njt.hu", "https://oif.gov.hu"} <= {p["url"] for p in hungary["portals"]}


def test_today_is_a_date():
    assert isinstance(datetime.date.today(), datetime.date)
    assert isinstance(tools.today(), datetime.date)


def test_fold_removes_accents():
    assert tools.fold("Hiánypótlás") == "hianypotlas"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Hungary", "Hungary"),
        ("hu", "Hungary"),
        ("Magyarország", "Hungary"),
        ("USA", "United States"),
        ("UK", "United Kingdom"),
        ("Czechia", "Czech Republic"),
        ("Türkiye", "Turkey"),
        ("Swaziland", "Eswatini"),
        ("Macedonia", "North Macedonia"),
    ],
)
def test_lookup_finds_countries(query, expected):
    assert expected in json.loads(tools.lookup_country(query))


def test_lookup_partial_and_word_matches():
    assert len(json.loads(tools.lookup_country("congo"))) == 2
    result = json.loads(tools.lookup_country("land of hungary"))
    assert "Hungary" in result
    assert len(result) <= 5


def test_lookup_lists_and_misses():
    listing = json.loads(tools.lookup_country("all"))
    assert listing["total_countries"] == 195
    assert json.loads(tools.lookup_country(""))["total_countries"] == 195
    assert "No portal list found for 'Atlantis'" in tools.lookup_country("Atlantis")
    assert "No portal list found" in tools.lookup_country("zz")


def test_lookup_missing_database(tmp_path):
    assert tools.lookup_country("Hungary", path=str(tmp_path / "nope.json")).startswith("error")


def test_lookup_custom_database(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"Narnia": {"code": "NA", "portals": []}}), encoding="utf-8")
    assert "Narnia" in tools.lookup_country("na", path=str(path))


def test_find_countries_empty_query():
    assert tools.find_countries("   ", {"A": {}}) == []


def test_tool_definitions_have_schemas():
    portal = tools.country_legal_portal_tool()
    assert portal["name"] == "lookup_country_legal_portal"
    assert portal["schema"]["function"]["parameters"]["required"] == ["country"]
    assert [t["name"] for t in tools.date_tools()] == ["days_until", "add_days"]


def test_days_until(fixed_today):
    upcoming = json.loads(tools.days_until("2026-11-11"))
    assert upcoming["days_left"] == 11 and upcoming["status"] == "upcoming"
    assert upcoming["weekday"] == "Wednesday"
    assert json.loads(tools.days_until("2026-10-31"))["status"] == "today"
    assert json.loads(tools.days_until("2026-10-01"))["status"] == "passed"
    assert tools.days_until("tomorrow").startswith("error")


def test_add_days(fixed_today):
    assert json.loads(tools.add_days("2026-10-28", 14))["result"] == "2026-11-11"
    assert json.loads(tools.add_days("2026-10-28", "-3"))["result"] == "2026-10-25"
    assert tools.add_days("nope", 3).startswith("error")
    assert tools.add_days("2026-10-28", "x").startswith("error")
    assert tools.add_days("2026-10-28", None).startswith("error")


@pytest.fixture
def rules(tmp_path):
    (tmp_path / "a.md").write_text("Intro\nHiánypótlás must be answered in 15 days.\nEnd", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("deadline deadline deadline\nrent", encoding="utf-8")
    (tmp_path / "c.json").write_text('{"deadline": 1}', encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.md").write_text("deadline", encoding="utf-8")
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, tmp_path / "link.md")
    return {t["name"]: t["fn"] for t in tools.folder_tools(str(tmp_path))}


def test_folder_list_and_read(rules):
    assert rules["list_files"]().splitlines() == ["a.md", "link.md", "sub/b.txt"]
    assert "Hiánypótlás" in rules["read_file"]("a.md")
    assert rules["read_file"]("sub/b.txt").startswith("deadline")


def test_folder_read_blocks_bad_paths(rules):
    assert rules["read_file"]("../outside.md") == "error: path outside folder"
    assert rules["read_file"]("link.md") == "error: path outside folder"
    assert rules["read_file"]("") == "error: path outside folder"
    assert rules["read_file"]("c.json") == "error: file not found"
    assert rules["read_file"]("missing.md") == "error: file not found"
    assert rules["read_file"]("sub") == "error: file not found"


def test_folder_search_ranks_and_folds_accents(rules):
    first = rules["search_docs"]("deadline rent").splitlines()
    assert first[0].startswith("sub/b.txt: deadline deadline deadline")
    assert "a.md" in rules["search_docs"]("hianypotlas 15 days")
    assert rules["search_docs"]("zzzzz") == "no matches"
    assert rules["search_docs"]("a") == "no matches"


def test_folder_tools_on_empty_folder(tmp_path):
    names = {t["name"]: t["fn"] for t in tools.folder_tools(str(tmp_path))}
    assert names["list_files"]() == "no files"


def test_date_hints_lists_valid_unique_dates(monkeypatch):
    monkeypatch.setattr(tools, "today", lambda: datetime.date(2026, 10, 31))
    text = "Due 2026-11-11, again 2026.11.11, old 2026/10/20, today 2026. 10. 31, bad 2026-02-31"
    hints = tools.date_hints(text)
    assert hints.splitlines()[0] == "Today is 2026-10-31. Dates found in the document:"
    assert "2026-10-20 (Tuesday): 11 days ago" in hints
    assert "2026-10-31 (Saturday): today" in hints
    assert hints.count("2026-11-11") == 1 and "2026-02-31" not in hints


def test_date_hints_empty_and_limited(monkeypatch):
    assert tools.date_hints("no dates") == "" and tools.date_hints(None) == ""
    many = " ".join(f"2027-01-{day:02d}" for day in range(1, 21))
    assert len(tools.date_hints(many, limit=3).splitlines()) == 4


def test_read_notes_inlines_small_folders_only(tmp_path):
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    (tmp_path / "c.json").write_text("{}", encoding="utf-8")
    notes = tools.read_notes(str(tmp_path))
    assert notes == "## a.md\nalpha\n\n## b.txt\nbeta"
    assert tools.read_notes(str(tmp_path), limit=5) == ""
    assert tools.read_notes(str(tmp_path / "missing")) == ""


def test_read_notes_gives_up_on_unreadable_files(tmp_path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe\x00")
    assert tools.read_notes(str(tmp_path)) == ""
    (tmp_path / "dir.md").mkdir()
    (tmp_path / "bad.md").unlink()
    assert tools.read_notes(str(tmp_path)) == ""
