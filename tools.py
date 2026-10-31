import datetime
import functools
import json
import os
import re
import unicodedata

from config import ROOT

PORTALS_PATH = os.path.join(ROOT, "rules", "country_legal_portals.json")
TEXT_SUFFIXES = (".md", ".txt", ".rst", ".csv", ".yaml", ".yml")
STOP_WORDS = {"republic", "of", "the", "and", "state", "states"}

ALIASES = {
    "us": "United States",
    "usa": "United States",
    "america": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "uae": "United Arab Emirates",
    "emirates": "United Arab Emirates",
    "holland": "Netherlands",
    "the netherlands": "Netherlands",
    "czechia": "Czech Republic",
    "korea": "Korea, South",
    "south korea": "Korea, South",
    "republic of korea": "Korea, South",
    "drc": "Congo, Democratic Republic of the",
    "dr congo": "Congo, Democratic Republic of the",
    "ivory coast": "Cote d'Ivoire",
    "russian federation": "Russia",
    "deutschland": "Germany",
    "magyarorszag": "Hungary",
    "espana": "Spain",
    "italia": "Italy",
    "frankreich": "France",
    "polska": "Poland",
    "turkiye": "Turkey",
    "burma": "Myanmar",
    "swaziland": "Eswatini",
}

URL_RE = re.compile(r"https?://[^\s)\],;]+")


def today():
    return datetime.date.today()


def fold(text):
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


@functools.lru_cache(maxsize=4)
def load_portals(path=PORTALS_PATH):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def find_countries(query, data):
    query = fold(query).strip()
    if not query:
        return []
    query = fold(ALIASES.get(query, query))
    for name, details in data.items():
        if fold(name) == query or details.get("code", "").lower() == query:
            return [name]
    if len(query) >= 4:
        partial = [name for name in data if query in fold(name)]
        if partial:
            return partial[:5]
    words = [w for w in query.split() if len(w) >= 4 and w not in STOP_WORDS]
    return [name for name in data if any(w in fold(name) for w in words)][:5]


def lookup_country(country="", path=PORTALS_PATH):
    data = load_portals(path)
    if not data:
        return "error: country portals database not found"
    query = (country or "").strip()
    if not query or query.lower() in {"all", "list", "*"}:
        return json.dumps({"total_countries": len(data), "countries": sorted(data)}, indent=2)
    names = find_countries(query, data)
    if not names:
        return (
            f"No portal list found for '{country}'. "
            f"{len(data)} countries are available; query 'all' to list them."
        )
    return json.dumps({name: data[name] for name in names}, indent=2, ensure_ascii=False)


def _tool(name, fn, description, properties, required):
    return {
        "name": name,
        "fn": fn,
        "schema": {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        },
    }


def country_legal_portal_tool():
    return _tool(
        "lookup_country_legal_portal",
        lookup_country,
        "Look up the starter list of official gazette, immigration, housing and tax portals "
        "for a country. Links in the result are the only links you may cite. Some entries "
        "have no link; say so instead of inventing one.",
        {"country": {"type": "string", "description": "Country name, alias or 2-letter code."}},
        ["country"],
    )


def parse_date(value):
    return datetime.date.fromisoformat(str(value).strip())


DATE_PATTERN = re.compile(r"(?<!\d)(\d{4})[-./]\s?(\d{1,2})[-./]\s?(\d{1,2})(?!\d)")


def date_hints(text, limit=12):
    now = today()
    seen = {}
    for year, month, day in DATE_PATTERN.findall(text or ""):
        try:
            found = datetime.date(int(year), int(month), int(day))
        except ValueError:
            continue
        seen.setdefault(found, None)
    lines = []
    for found in sorted(seen)[:limit]:
        left = (found - now).days
        when = "today" if left == 0 else f"{abs(left)} days {'ago' if left < 0 else 'from today'}"
        lines.append(f"{found.isoformat()} ({found.strftime('%A')}): {when}")
    if not lines:
        return ""
    return f"Today is {now.isoformat()}. Dates found in the document:\n" + "\n".join(lines)


def days_until(date):
    try:
        target = parse_date(date)
    except ValueError:
        return "error: dates must look like YYYY-MM-DD"
    now = today()
    left = (target - now).days
    status = "passed" if left < 0 else "today" if left == 0 else "upcoming"
    return json.dumps(
        {
            "date": target.isoformat(),
            "weekday": target.strftime("%A"),
            "today": now.isoformat(),
            "days_left": left,
            "status": status,
        }
    )


def add_days(date, days):
    try:
        start = parse_date(date)
        count = int(days)
    except (TypeError, ValueError):
        return "error: need a YYYY-MM-DD date and a whole number of days"
    end = start + datetime.timedelta(days=count)
    return json.dumps({"start": start.isoformat(), "days": count, "result": end.isoformat(),
                       "weekday": end.strftime("%A")})


def date_tools():
    return [
        _tool(
            "days_until",
            days_until,
            "Count the calendar days from today to a date. Use this instead of doing date math.",
            {"date": {"type": "string", "description": "YYYY-MM-DD"}},
            ["date"],
        ),
        _tool(
            "add_days",
            add_days,
            "Add calendar days to a date, for example to turn 'within 14 days' into a date.",
            {"date": {"type": "string", "description": "YYYY-MM-DD"},
             "days": {"type": "integer"}},
            ["date", "days"],
        ),
    ]


def read_notes(root, limit=8000):
    try:
        names = sorted(n for n in os.listdir(root) if n.endswith((".md", ".txt")))
    except OSError:
        return ""
    parts = []
    for name in names:
        try:
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                parts.append(f"## {name}\n{handle.read().strip()}")
        except (OSError, UnicodeDecodeError):
            return ""
    text = "\n\n".join(parts)
    return text if len(text) <= limit else ""


def folder_tools(root):
    root = os.path.realpath(root)

    def walk():
        for folder, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in {".git", ".venv", "__pycache__"})
            for name in sorted(files):
                if name.endswith(TEXT_SUFFIXES):
                    yield os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/")

    def list_files():
        return "\n".join(walk()) or "no files"

    def read_file(path):
        full = os.path.realpath(os.path.join(root, str(path)))
        if not full.startswith(root + os.sep):
            return "error: path outside folder"
        if not full.endswith(TEXT_SUFFIXES) or not os.path.isfile(full):
            return "error: file not found"
        with open(full, encoding="utf-8", errors="replace") as handle:
            return handle.read(20000)

    def search_docs(query):
        words = [w for w in re.findall(r"\w+", fold(query)) if len(w) > 2]
        scored = []
        for rel in walk():
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
            folded = [fold(line) for line in lines]
            score = sum(line.count(w) for line in folded for w in words)
            if score:
                best = max(range(len(lines)), key=lambda i: sum(w in folded[i] for w in words))
                scored.append((score, rel, lines[best].strip()[:180]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return "\n".join(f"{rel}: {line}" for _, rel, line in scored[:5]) or "no matches"

    return [
        _tool("list_files", list_files, "List the rule files available.", {}, []),
        _tool(
            "read_file",
            read_file,
            "Read one rule file by relative path.",
            {"path": {"type": "string"}},
            ["path"],
        ),
        _tool(
            "search_docs",
            search_docs,
            "Search the rule files by keyword. Accents are ignored.",
            {"query": {"type": "string"}},
            ["query"],
        ),
    ]
