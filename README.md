# LetterLens

Official letters, tenancy contracts, utility notices, and government residency documents are hard to read and critical deadlines are easy to miss. **LetterLens** is a multimodal AI agent that takes pictures (photos, scans), Markdown files (`.md`), or plain text, and explains what the document asks, the deadlines, any money required, and what happens next in clear, simple **A1–B2 plain English**.

## How it works

- **Model:** `gemma-4-26b-a4b-it` (Gemma 4, 26B mixture-of-experts with 4B active parameters), open weights under the [Gemma terms](https://ai.google.dev/gemma/terms).
- **A1–B2 Plain English:** Explanations are written in clear, direct English with short sentences, structured bullet points, and simplified explanations of any legal or bureaucratic term.
- **Multimodal Inputs:** Ingests document scans and photos (`.png`, `.jpg`, `.webp`), Markdown files (`.md`), and raw text.
- **Global Country Legal Repository:** Includes a verified primary database of official legal gazettes, immigration departments, tenancy tribunals, and tax portals for 195+ countries (`rules/country_legal_portals.json`), accessed via the `lookup_country_legal_portal` tool.
- **Footnotes & Citations:** Backs every legal rule, decree, and official portal with inline citation markers (`[^1]`, `[^2]`) and structured footnote lists.
- **Strict Legal Guardrails:** Strictly restricts reasoning to legal, administrative, governmental, tenancy, and contractual documents, refusing casual or off-topic queries.
- **Local OCR Acceleration:** Automatically extracts text from pictures locally using native OS OCR before sending to Gemma, accelerating understanding and grounding numbers, dates, and reference codes.
- **Secrets Pool Load Balancing:** Supports multiple API keys (`GEMINI_1_KEY`, `GEMINI_2_KEY`, etc.) with automatic round-robin rotation and failover.
- **Clean Markdown Output:** Generates structured Markdown responses featuring summaries, action items, deadlines, fee tables, and legal disclaimers. The web interface includes a real-time Markdown-to-HTML parser.
- **Zero Third-Party Runtime Dependencies:** Self-contained standard library architecture.
- **SynthID-Free Clean Codebase:** Code contains zero comments or synthids.

## Run it

```bash
# 1. Start the web chat UI (http://127.0.0.1:8080):
python app.py serve

# 2. Or run the agent interactively in terminal:
python agent.py

# 3. Or analyze directly from CLI:
python agent.py samples/sample_letter_scan.png   # picture with local OCR + vision
python agent.py samples/sample_document.md       # markdown document
python agent.py "What happens if I miss a residence permit deadline?"
```

## Configuration

Keys are loaded from `.env` or environment variables:

```bash
GEMINI_1_KEY=your_first_gemini_key
GEMINI_2_KEY=your_second_gemini_key
```

Optional rules directory:
```bash
export LETTERLENS_RULES=./rules
```

## Tests

```bash
python -m unittest tests/test_app.py
```

## Licence

Apache License 2.0, see `LICENSE` and `NOTICE`.
