# LetterLens

Legal documents are hard to read, and the deadline is easy to miss. LetterLens takes a photo, a scan, a `.md`/`.txt` file or pasted text of a legal document (a notice, a contract, a permit, a bill), and explains it in plain English (A1 to B2): who sent it, what it asks, the deadline, any money, and what happens next. It starts from Hungary (rent, residence permits, municipal notices) and can be pointed at any other country.

## How the model is used

- **Model:** `gemma-4-26b-a4b-it` (Gemma 4, 26B mixture of experts, 4B active parameters). Open weights under the [Gemma terms](https://ai.google.dev/gemma/terms).
- **How it is called:** through the Gemini API (OpenAI-compatible endpoint), with streaming answers and tool calls. Gemma reads the document, calls tools when it needs them, and writes the answer.
- **Offline:** `--offline` sends everything to a local OpenAI-compatible server instead (Ollama by default, `http://127.0.0.1:11434/v1`). Nothing leaves the machine. Set `LETTERLENS_LOCAL_MODEL` to the model you pulled (default `gemma3:12b`, which is a placeholder; use any Gemma tag you have).

## What you get

- An **At a glance** card first: sender, what it is, deadline, amount, urgency, next step. One click adds the deadline to your calendar (`.ics`).
- A short explanation, a to-do list, and a check for scams or odd requests.
- Quoted lines are checked against your document. If a quote is not in it, you get a warning.
- Sources are only links the tools returned. The model is told not to invent any.
- Today's date is known to the agent. Dates found in the document are turned into "days left" in code before the model runs (plus `days_until` and `add_days` tools), so "you have 9 days" is computed, not guessed.
- Every answer shows how long it took, and when the first words arrived (also printed by the CLI).
- Country context is set per chat. Hungary is the default.

## Tools the agent can call

| Tool | Purpose |
|---|---|
| `lookup_country_legal_portal` | Official portals for a country, from `rules/country_legal_portals.json` |
| `days_until`, `add_days` | Date arithmetic against today |
| `list_files`, `read_file`, `search_docs` | Only used when `rules/` is too big to fit in the prompt (accent-insensitive, ranked, read-only, stays inside the folder). Small rule folders are put straight into the prompt to save a round trip |

## Run it

Python 3.11 or newer. No runtime dependencies.

```bash
python app.py serve                    # web app on http://127.0.0.1:8080
python app.py serve --offline          # same, with a local model
python agent.py samples/sample_letter_hu.txt
python agent.py samples/sample_letter_scan.png "What is the deadline?" --country Hungary
python agent.py -i                     # chat in the terminal
```

Keys go in `.env` or the environment (any variable that looks like `GEMINI…KEY`, comma-separated lists allowed, or `LETTERLENS_API_KEY`). Several keys are balanced: each request goes to the least busy healthy key, a rate-limited key (429) is benched for its `Retry-After` time (default 20 s) and the request switches to the other key at once, and a rejected key (401/403) is benched for 10 minutes.

| Variable | Meaning |
|---|---|
| `LETTERLENS_MODEL` | Cloud model name |
| `LETTERLENS_BASE_URL` | Cloud endpoint |
| `LETTERLENS_LOCAL_URL`, `LETTERLENS_LOCAL_MODEL` | Offline server and model |
| `LETTERLENS_RULES` | Folder with the rules notes |
| `LETTERLENS_REASONING` | `reasoning_effort` sent to the model (default `low`, `off` to skip; dropped automatically if the API rejects it) |
| `LETTERLENS_OCR` | `off` to skip local OCR |
| `LETTERLENS_OCR_LANG`, `LETTERLENS_TESSERACT_LANG` | OCR language |
| `LETTERLENS_HOST`, `LETTERLENS_PORT` | Server address |

## Speed and safety

- Answers stream as they are written. Images are shrunk in the browser before upload, and OCR runs in parallel, is cached and gives up after 8 s.
- Fewer model round trips: country info, rule notes and date maths are put in the prompt up front instead of fetched with tool calls.
- Static files are gzipped and cached with ETags; connections are kept alive.
- The server only accepts `data:` images, checks `Host` and `Origin` (DNS rebinding and cross-site posts), limits sizes, sends a strict Content-Security-Policy, and renders answers through an escaping renderer.
- Documents are treated as untrusted text. The prompt tells the model to ignore instructions inside them.

## Limits, said plainly

- Not legal advice. Every answer ends with a disclaimer.
- No PDF support yet. Export a page as an image.
- Local OCR: Windows uses `Windows.Media.Ocr`; other systems use Tesseract if installed (`pip install ".[ocr]"` plus the Tesseract program). Without OCR the model still reads the image itself.
- The notes in `rules/` are **starter data**. Only lines marked `[verified]` come from an official page; the rest is demo text. Check the official portal before you act.
- Personal data in a document is sent to the model you choose. Use `--offline` for sensitive documents.
- Live Gemma behaviour was tested against a fake server in the test suite, not against a paid API key.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest        # 100% line and branch coverage is enforced
node --test tests/js/render.test.js
ruff check .
```

The Python suite covers the agent, client, server, OCR, tools and config with a fake model server. The renderer (`static/render.js`) has its own Node tests; the browser glue in `static/app.js` is not unit tested.

## Built with

Written with AI assistance (Claude Code). Tools: Python standard library, pytest, pytest-cov, ruff, Node's test runner. See `NOTICE` and `LICENSE` (Apache 2.0).
