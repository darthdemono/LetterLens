import base64
import io
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import agent
from agent import (
    Client,
    GeminiClient,
    GemmaAgent,
    KeyPool,
    LocalOCR,
    MarkdownParser,
    MODEL,
    SYSTEM_PROMPT as SYSTEM,
    URL,
    describe,
    country_legal_portal_tool,
)

Agent = GemmaAgent
IMAGE_EXTS = agent.IMAGE_EXTS
TEXT_EXTS = agent.TEXT_EXTS
MARKDOWN_EXTS = agent.MARKDOWN_EXTS


def folder_tools(root):
    root = os.path.realpath(root)
    text_exts = (".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml")

    def _walk():
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in {".git", ".venv", "__pycache__"}]
            for n in sorted(files):
                if n.endswith(text_exts):
                    yield os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/")

    def list_files():
        return "\n".join(sorted(_walk())) or "no files"

    def read_file(path: str):
        p = os.path.realpath(os.path.join(root, path))
        if p != root and not p.startswith(root + os.sep):
            return "error: path outside folder"
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read(20000)

    def search_docs(query: str):
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        hits = []
        for rel in _walk():
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            score = sum(ln.lower().count(w) for ln in lines for w in words)
            if score:
                best = max(lines, key=lambda l: sum(w in l.lower() for w in words))
                hits.append(f"{rel}: {best.strip()[:180]}")
        return "\n".join(hits[:5]) or "no matches"

    return [
        {
            "name": "list_files",
            "fn": list_files,
            "schema": {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List readable text files in the rules folder.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        },
        {
            "name": "read_file",
            "fn": read_file,
            "schema": {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a text file from the rules folder by relative path.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
        },
        {
            "name": "search_docs",
            "fn": search_docs,
            "schema": {
                "type": "function",
                "function": {
                    "name": "search_docs",
                    "description": "Keyword search over the rules folder.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
        },
    ]


def build_agent(client=None):
    a = GemmaAgent(client or GeminiClient(), system=SYSTEM)
    a.add_tools([country_legal_portal_tool()])
    rules = os.environ.get("LETTERLENS_RULES")
    if not rules:
        default_rules = os.path.join(ROOT, "rules")
        if os.path.isdir(default_rules):
            rules = default_rules
    if rules and os.path.isdir(rules):
        a.add_tools(folder_tools(rules))
    return a


SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}
SKIP_NAMES = (".env", "CLAUDE", "AGENTS", "CODEX", "GEMINI")
ROOT = os.path.dirname(os.path.abspath(__file__))


def source_zip(root=ROOT):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(files):
                if name.startswith(SKIP_NAMES) or name.endswith(".pyc"):
                    continue
                path = os.path.join(dirpath, name)
                z.write(path, os.path.join("letterlens", os.path.relpath(path, root)))
    return buf.getvalue()


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LetterLens - Simple Document Intelligence</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-app: #080c14;
  --bg-surface: #0f172a;
  --bg-surface-elevated: #162036;
  --border-subtle: #1e293b;
  --border-active: #334155;
  --border-highlight: #2563eb;
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --text-dim: #64748b;
  --accent-blue: #2563eb;
  --accent-blue-hover: #1d4ed8;
  --accent-cyan: #38bdf8;
  --table-border: #243247;
  --table-head-bg: #131d31;
  --table-alt-bg: rgba(30, 41, 59, 0.35);
  --error: #ef4444;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  background-color: var(--bg-app);
  color: var(--text-main);
  min-height: 100vh;
  padding: 1.5rem 1rem;
  display: flex;
  flex-direction: column;
}

.container {
  max-width: 56rem;
  width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  flex: 1;
}

header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 1.25rem;
  margin-bottom: 1.25rem;
}

.brand {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.logo-row {
  display: flex;
  align-items: center;
  gap: 0.65rem;
}

.logo-icon {
  width: 24px;
  height: 24px;
  color: var(--accent-cyan);
}

h1 {
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: -0.025em;
  color: var(--text-main);
}

.badge-model {
  background: rgba(37, 99, 235, 0.15);
  border: 1px solid rgba(59, 130, 246, 0.3);
  color: #93c5fd;
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.18rem 0.55rem;
  border-radius: 9999px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.tagline {
  font-size: 0.88rem;
  color: var(--text-muted);
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  background: var(--bg-surface);
  color: var(--text-muted);
  border: 1px solid var(--border-subtle);
  padding: 0.48rem 0.88rem;
  border-radius: 0.5rem;
  font-size: 0.82rem;
  font-weight: 500;
  text-decoration: none;
  transition: all 0.15s ease;
  cursor: pointer;
}

.btn-secondary:hover {
  background: var(--border-subtle);
  color: var(--text-main);
  border-color: var(--border-active);
}

.btn-secondary svg {
  width: 14px;
  height: 14px;
}

.sample-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.sample-label {
  font-size: 0.78rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
  margin-right: 0.25rem;
}

.pill-sample {
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid var(--border-subtle);
  color: #cbd5e1;
  font-size: 0.8rem;
  font-weight: 500;
  padding: 0.35rem 0.75rem;
  border-radius: 0.45rem;
  cursor: pointer;
  transition: all 0.15s ease;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.pill-sample:hover {
  background: var(--bg-surface-elevated);
  border-color: var(--accent-blue);
  color: var(--text-main);
}

#dropZone {
  border: 1.5px dashed var(--border-active);
  background: rgba(15, 23, 42, 0.5);
  border-radius: 0.75rem;
  padding: 1.15rem;
  text-align: center;
  margin-bottom: 1rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

#dropZone.dragover {
  border-color: var(--accent-cyan);
  background: rgba(56, 189, 248, 0.08);
}

.drop-primary {
  font-size: 0.92rem;
  font-weight: 600;
  color: #cbd5e1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
}

.drop-secondary {
  font-size: 0.78rem;
  color: var(--text-dim);
  margin-top: 0.25rem;
}

#log {
  flex: 1;
  min-height: 22rem;
  max-height: 58vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 0.5rem 0.25rem 1rem 0.25rem;
  margin-bottom: 0.75rem;
}

.msg-wrapper {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  max-width: 96%;
}

.msg-wrapper.u {
  align-self: flex-end;
  max-width: 82%;
}

.msg-wrapper.a {
  align-self: flex-start;
  width: 100%;
}

.msg-sender {
  font-size: 0.74rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  padding: 0 0.25rem;
}

.msg {
  padding: 1rem 1.25rem;
  border-radius: 0.75rem;
  line-height: 1.6;
  font-size: 0.95rem;
  word-break: break-word;
}

.u .msg {
  background: #182844;
  border: 1px solid #2b4c7e;
  color: #f8fafc;
  border-bottom-right-radius: 0.2rem;
}

.a .msg {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  color: #e2e8f0;
  border-bottom-left-radius: 0.2rem;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
}

.msg-attach {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.65rem;
}

.thumb-img {
  max-width: 160px;
  max-height: 110px;
  border-radius: 0.4rem;
  border: 1px solid var(--border-active);
  object-fit: cover;
}

.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  background: var(--bg-surface-elevated);
  border: 1px solid var(--border-active);
  color: #93c5fd;
  font-size: 0.78rem;
  padding: 0.25rem 0.55rem;
  border-radius: 0.4rem;
}

.md-h2, .md-h3, .md-h4 {
  color: #f8fafc;
  font-weight: 700;
  letter-spacing: -0.015em;
  margin: 1rem 0 0.45rem 0;
}
.md-h2 { font-size: 1.15rem; border-bottom: 1px solid var(--border-subtle); padding-bottom: 0.3rem; }
.md-h3 { font-size: 1.05rem; color: #93c5fd; }
.md-h4 { font-size: 0.95rem; }
.md-p { margin-bottom: 0.75rem; }
.md-p:last-child { margin-bottom: 0; }
.md-ul, .md-ol {
  margin: 0.35rem 0 0.75rem 1.4rem;
}
.md-li {
  margin-bottom: 0.35rem;
}
.md-li strong { color: #f8fafc; }
.md-strong { font-weight: 600; color: #f8fafc; }
.md-em { font-style: italic; color: #cbd5e1; }
.md-code {
  font-family: ui-monospace, "Cascadia Code", "Fira Code", monospace;
  background: rgba(30, 41, 59, 0.7);
  padding: 0.15rem 0.35rem;
  border-radius: 0.3rem;
  font-size: 0.85em;
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.2);
}
.md-hr {
  border: none;
  border-top: 1px solid var(--border-subtle);
  margin: 1.1rem 0;
}
.md-disclaimer {
  background: rgba(15, 23, 42, 0.7);
  border-left: 3px solid #64748b;
  padding: 0.65rem 0.85rem;
  margin: 0.85rem 0 0.35rem 0;
  font-size: 0.84rem;
  color: var(--text-muted);
  border-radius: 0 0.4rem 0.4rem 0;
}

.md-fnref {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--accent-cyan);
  text-decoration: none;
  padding: 0 0.15rem;
  vertical-align: super;
  line-height: 0;
}
.md-fnref:hover {
  color: #93c5fd;
  text-decoration: underline;
}

.md-footnotes {
  margin-top: 1.15rem;
  padding-top: 0.85rem;
  border-top: 1px solid var(--border-subtle);
  font-size: 0.82rem;
  color: var(--text-muted);
}
.md-footnotes-title {
  font-size: 0.84rem;
  font-weight: 600;
  color: #93c5fd;
  margin-bottom: 0.45rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.md-fn-item {
  margin-bottom: 0.35rem;
  line-height: 1.5;
}
.md-fn-num {
  font-weight: 600;
  color: var(--accent-cyan);
  margin-right: 0.25rem;
}
.fn-back {
  color: var(--text-dim);
  text-decoration: none;
  margin-left: 0.35rem;
}
.fn-back:hover {
  color: var(--accent-cyan);
}

.table-container {
  overflow-x: auto;
  margin: 0.85rem 0;
  border: 1px solid var(--table-border);
  border-radius: 0.5rem;
}
.md-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.86rem;
  text-align: left;
}
.md-table th {
  background: var(--table-head-bg);
  color: #93c5fd;
  font-weight: 600;
  padding: 0.55rem 0.8rem;
  border-bottom: 1px solid var(--table-border);
}
.md-table td {
  padding: 0.55rem 0.8rem;
  border-bottom: 1px solid var(--table-border);
  color: #e2e8f0;
}
.md-table tr:nth-child(even) td {
  background: var(--table-alt-bg);
}
.md-table tr:last-child td {
  border-bottom: none;
}

#previewBar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0 0.25rem 0.65rem 0.25rem;
  min-height: 0;
}

.preview-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  background: var(--bg-surface);
  border: 1px solid var(--accent-blue);
  color: var(--text-main);
  padding: 0.28rem 0.65rem;
  border-radius: 0.45rem;
  font-size: 0.82rem;
  font-weight: 500;
}

.preview-chip img {
  width: 22px;
  height: 22px;
  border-radius: 0.25rem;
  object-fit: cover;
}

.preview-chip .close-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  padding: 0 0.15rem;
  transition: color 0.15s;
}

.preview-chip .close-btn:hover {
  color: var(--error);
}

form {
  display: flex;
  gap: 0.65rem;
  background: var(--bg-surface);
  padding: 0.55rem 0.65rem;
  border-radius: 0.75rem;
  border: 1px solid var(--border-subtle);
  align-items: center;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}

.btn-icon {
  background: var(--bg-surface-elevated);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  width: 40px;
  height: 40px;
  border-radius: 0.5rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.btn-icon:hover {
  background: var(--border-subtle);
  color: var(--text-main);
  border-color: var(--border-active);
}

.btn-icon svg {
  width: 18px;
  height: 18px;
}

input[type="text"] {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text-main);
  font-size: 0.95rem;
  font-family: inherit;
  padding: 0.4rem 0.5rem;
}

input[type="text"]::placeholder {
  color: var(--text-dim);
}

.btn-submit {
  background: var(--accent-blue);
  color: #fff;
  border: none;
  border-radius: 0.5rem;
  padding: 0.55rem 1.25rem;
  font-size: 0.9rem;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.45rem;
  transition: background 0.15s ease;
  flex-shrink: 0;
}

.btn-submit:hover {
  background: var(--accent-blue-hover);
}

.btn-submit:disabled {
  background: var(--border-subtle);
  color: var(--text-dim);
  cursor: not-allowed;
}

.btn-submit svg {
  width: 15px;
  height: 15px;
}

.err {
  color: var(--error) !important;
}
</style>
</head>
<body>
<div class="container">
  <header>
    <div class="brand">
      <div class="logo-row">
        <svg class="logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="16" y1="13" x2="8" y2="13"></line>
          <line x1="16" y1="17" x2="8" y2="17"></line>
          <polyline points="10 9 9 9 8 9"></polyline>
        </svg>
        <h1>LetterLens</h1>
        <span class="badge-model">Gemma 4 Multimodal</span>
      </div>
      <div class="tagline">Plain English document intelligence agent with country legal repositories</div>
    </div>
    <a href="/letterlens.zip" download class="btn-secondary" title="Export source code">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="7 10 12 15 17 10"></polyline>
        <line x1="12" y1="15" x2="12" y2="3"></line>
      </svg>
      Export Code
    </a>
  </header>

  <div class="sample-bar">
    <span class="sample-label">Quick Samples:</span>
    <button type="button" class="pill-sample" id="sampleMdBtn">
      Notice Document (.md)
    </button>
    <button type="button" class="pill-sample" id="sampleImgBtn">
      Scanned Letter (Image)
    </button>
    <button type="button" class="pill-sample" id="sampleTxtBtn">
      Local Government Inquiry
    </button>
  </div>

  <div id="dropZone">
    <div class="drop-primary">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="17 8 12 3 7 8"></polyline>
        <line x1="12" y1="3" x2="12" y2="15"></line>
      </svg>
      Drag and drop pictures, Markdown (.md), or text files here
    </div>
    <div class="drop-secondary">Supports PNG, JPG, WEBP scans with local OCR, Markdown documents, and plain text. Paste (Ctrl+V) anywhere.</div>
  </div>

  <div id="log">
    <div class="msg-wrapper a">
      <div class="msg-sender">LetterLens Agent</div>
      <div class="msg">
        <div class="md-p"><strong>LetterLens Agent ready.</strong> Provide an official letter, notice, bill, or legal document as a <strong>picture</strong>, <strong>Markdown file (.md)</strong>, or <strong>plain text</strong>.</div>
        <div class="md-p">The agent consults official country legal portals as its primary source of truth and explains documents in clear A1-B2 plain English with verified footnotes.</div>
      </div>
    </div>
  </div>

  <div id="previewBar"></div>

  <form id="f" action="javascript:void(0);" onsubmit="handleSubmit(event);">
    <input type="file" id="filePicker" multiple accept="image/*,.md,.txt,.json,.csv" style="display:none">
    <button type="button" class="btn-icon" id="attachBtn" title="Attach file or picture">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
      </svg>
    </button>
    <input id="m" type="text" placeholder="Type a message or paste document text..." autocomplete="off" autofocus>
    <button id="sendBtn" class="btn-submit" type="submit">
      <span>Explain</span>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="22" y1="2" x2="11" y2="13"></line>
        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
      </svg>
    </button>
  </form>
</div>

<script>
const log = document.getElementById('log');
const form = document.getElementById('f');
const input = document.getElementById('m');
const sendBtn = document.getElementById('sendBtn');
const dropZone = document.getElementById('dropZone');
const filePicker = document.getElementById('filePicker');
const attachBtn = document.getElementById('attachBtn');
const previewBar = document.getElementById('previewBar');
const sid = Math.random().toString(36).slice(2);

let pendingImages = [];
let pendingFiles = [];

attachBtn.addEventListener('click', () => filePicker.click());
dropZone.addEventListener('click', () => filePicker.click());

['dragenter', 'dragover'].forEach(name => {
  dropZone.addEventListener(name, (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
});
['dragleave', 'drop'].forEach(name => {
  dropZone.addEventListener(name, (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
  });
});

dropZone.addEventListener('drop', (e) => {
  if (e.dataTransfer && e.dataTransfer.files) {
    handleFiles(e.dataTransfer.files);
  }
});

filePicker.addEventListener('change', (e) => {
  if (e.target.files) {
    handleFiles(e.target.files);
    e.target.value = '';
  }
});

window.addEventListener('paste', (e) => {
  if (e.clipboardData && e.clipboardData.files && e.clipboardData.files.length > 0) {
    handleFiles(e.clipboardData.files);
  }
});

document.getElementById('sampleMdBtn').addEventListener('click', async () => {
  try {
    const res = await fetch('/sample/notice.md');
    if (res.ok) {
      const text = await res.text();
      pendingFiles.push({ name: 'lease_amendment_notice.md', content: text });
      renderPreviews();
      input.value = 'Explain this notice in simple English and cite official legal portals with footnotes.';
      input.focus();
    }
  } catch (err) {
    console.error(err);
  }
});

document.getElementById('sampleImgBtn').addEventListener('click', async () => {
  try {
    const res = await fetch('/sample/scan.png');
    if (res.ok) {
      const dataUrl = await res.text();
      pendingImages.push({ name: 'sample_letter_scan.png', dataUrl: dataUrl });
      renderPreviews();
      input.value = 'Explain this scanned letter in simple English and cite official country legal portals in footnotes.';
      input.focus();
    }
  } catch (err) {
    console.error(err);
  }
});

document.getElementById('sampleTxtBtn').addEventListener('click', () => {
  input.value = 'A tenant in Germany received an official utility adjustment notice (Nebenkostenabrechnung). What are the official legal portals and rights for contesting it under German tenancy law?';
  input.focus();
});

function handleFiles(files) {
  for (const f of files) {
    if (f.type.startsWith('image/')) {
      const reader = new FileReader();
      reader.onload = () => {
        pendingImages.push({ name: f.name, dataUrl: reader.result });
        renderPreviews();
      };
      reader.readAsDataURL(f);
    } else {
      const reader = new FileReader();
      reader.onload = () => {
        pendingFiles.push({ name: f.name, content: reader.result });
        renderPreviews();
      };
      reader.readAsText(f);
    }
  }
}

function renderPreviews() {
  previewBar.innerHTML = '';
  pendingImages.forEach((img, idx) => {
    const chip = document.createElement('div');
    chip.className = 'preview-chip';
    chip.innerHTML = `<img src="${img.dataUrl}"> <span>${escapeHtml(img.name)}</span> <button type="button" class="close-btn" aria-label="Remove">&times;</button>`;
    chip.querySelector('button').onclick = () => {
      pendingImages.splice(idx, 1);
      renderPreviews();
    };
    previewBar.appendChild(chip);
  });
  pendingFiles.forEach((file, idx) => {
    const chip = document.createElement('div');
    chip.className = 'preview-chip';
    chip.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg> <span>${escapeHtml(file.name)}</span> <button type="button" class="close-btn" aria-label="Remove">&times;</button>`;
    chip.querySelector('button').onclick = () => {
      pendingFiles.splice(idx, 1);
      renderPreviews();
    };
    previewBar.appendChild(chip);
  });
}

function escapeHtml(str) {
  return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function parseMarkdown(md) {
  if (!md) return '';
  const lines = md.split('\\n');
  let html = '';
  let inList = false;
  let inTable = false;
  let tableRows = [];
  let footnoteDefs = [];

  function flushList() {
    if (inList) {
      html += '</ul>';
      inList = false;
    }
  }

  function flushTable() {
    if (inTable && tableRows.length > 0) {
      let tHtml = '<div class="table-container"><table class="md-table">';
      const isAlignRow = (cells) => cells.every(c => /^:?-+:?$/.test(c.trim()));
      let headerPassed = false;

      tableRows.forEach((rowCells, rIdx) => {
        if (rIdx === 1 && isAlignRow(rowCells)) {
          return;
        }
        if (!headerPassed) {
          tHtml += '<thead><tr>';
          rowCells.forEach(cell => {
            tHtml += `<th>${inlineFormat(cell.trim())}</th>`;
          });
          tHtml += '</tr></thead><tbody>';
          headerPassed = true;
        } else {
          tHtml += '<tr>';
          rowCells.forEach(cell => {
            tHtml += `<td>${inlineFormat(cell.trim())}</td>`;
          });
          tHtml += '</tr>';
        }
      });
      tHtml += '</tbody></table></div>';
      html += tHtml;
      tableRows = [];
      inTable = false;
    }
  }

  function inlineFormat(text) {
    let out = escapeHtml(text);
    out = out.replace(/`([^`]+)`/g, '<code class="md-code">$1</code>');
    out = out.replace(/\\*\\*([^\\*]+)\\*\\*/g, '<strong class="md-strong">$1</strong>');
    out = out.replace(/(?:^|[^*])\\*([^*]+)\\*/g, ' <em class="md-em">$1</em>');
    out = out.replace(/\\[\\^(\\d+)\\]/g, '<sup><a href="#fn-$1" id="fnref-$1" class="md-fnref">[$1]</a></sup>');
    return out;
  }

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.trim();

    const fnDefMatch = line.match(/^\\[\\^(\\d+)\\]:\\s*(.*)$/);
    if (fnDefMatch) {
      flushList();
      flushTable();
      footnoteDefs.push({ id: fnDefMatch[1], text: fnDefMatch[2] });
      continue;
    }

    if (line.startsWith('|') && line.endsWith('|')) {
      flushList();
      inTable = true;
      const cells = line.split('|').slice(1, -1);
      tableRows.push(cells);
      continue;
    } else if (inTable) {
      flushTable();
    }

    if (!line) {
      flushList();
      continue;
    }

    if (line.startsWith('#### ')) {
      flushList();
      html += `<div class="md-h4">${inlineFormat(line.slice(5))}</div>`;
      continue;
    }
    if (line.startsWith('### ')) {
      flushList();
      html += `<div class="md-h3">${inlineFormat(line.slice(4))}</div>`;
      continue;
    }
    if (line.startsWith('## ')) {
      flushList();
      html += `<div class="md-h2">${inlineFormat(line.slice(3))}</div>`;
      continue;
    }

    if (/^(\*{3,}|-{3,}|_{3,})$/.test(line)) {
      flushList();
      html += '<hr class="md-hr">';
      continue;
    }

    if (line.toLowerCase().includes('disclaimer:')) {
      flushList();
      html += `<div class="md-disclaimer">${inlineFormat(line)}</div>`;
      continue;
    }

    const listMatch = rawLine.match(/^(\\s*)[*+-]\\s+(.*)$/);
    if (listMatch) {
      if (!inList) {
        html += '<ul class="md-ul">';
        inList = true;
      }
      const indent = listMatch[1].length;
      const margin = indent >= 2 ? ' style="margin-left: 1.25rem;"' : '';
      html += `<li class="md-li"${margin}>${inlineFormat(listMatch[2])}</li>`;
      continue;
    } else {
      flushList();
    }

    html += `<div class="md-p">${inlineFormat(line)}</div>`;
  }

  flushList();
  flushTable();

  if (footnoteDefs.length > 0) {
    let fnHtml = '<div class="md-footnotes"><div class="md-footnotes-title">Footnotes &amp; Verified Legal Portals</div>';
    footnoteDefs.forEach(fn => {
      fnHtml += `<div class="md-fn-item" id="fn-${fn.id}"><span class="md-fn-num">[${fn.id}]</span>${inlineFormat(fn.text)} <a href="#fnref-${fn.id}" class="fn-back" title="Back to text">&#8617;</a></div>`;
    });
    fnHtml += '</div>';
    html += fnHtml;
  }

  return html;
}

function addMsg(cls, text, attachedImages = [], attachedFiles = []) {
  const wrapper = document.createElement('div');
  wrapper.className = 'msg-wrapper ' + cls;

  const sender = document.createElement('div');
  sender.className = 'msg-sender';
  sender.textContent = cls === 'u' ? 'You' : 'LetterLens Agent';
  wrapper.appendChild(sender);

  const bubble = document.createElement('div');
  bubble.className = 'msg';

  if (attachedImages.length > 0 || attachedFiles.length > 0) {
    const attachDiv = document.createElement('div');
    attachDiv.className = 'msg-attach';
    attachedImages.forEach(img => {
      const el = document.createElement('img');
      el.src = img.dataUrl || img;
      el.className = 'thumb-img';
      attachDiv.appendChild(el);
    });
    attachedFiles.forEach(f => {
      const badge = document.createElement('div');
      badge.className = 'file-chip';
      badge.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg> <span>${escapeHtml(f.name)}</span>`;
      attachDiv.appendChild(badge);
    });
    bubble.appendChild(attachDiv);
  }

  const contentDiv = document.createElement('div');
  if (cls === 'u') {
    contentDiv.textContent = text;
  } else {
    contentDiv.innerHTML = parseMarkdown(text);
  }
  bubble.appendChild(contentDiv);
  wrapper.appendChild(bubble);

  log.appendChild(wrapper);
  log.scrollTop = log.scrollHeight;
  return contentDiv;
}

async function handleSubmit(e) {
  if (e && e.preventDefault) e.preventDefault();
  const text = (input.value || '').trim();
  if (!text && pendingImages.length === 0 && pendingFiles.length === 0) return false;

  const currentImages = [...pendingImages];
  const currentFiles = [...pendingFiles];
  pendingImages = [];
  pendingFiles = [];
  renderPreviews();

  input.value = '';
  addMsg('u', text || '(Analyzed attachments)', currentImages, currentFiles);
  const bubble = addMsg('a', 'LetterLens Agent is analyzing input with Gemma 4 and official legal portals...');
  sendBtn.disabled = true;

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sid: sid,
        message: text,
        images: currentImages.map(i => i.dataUrl),
        files: currentFiles
      })
    });

    if (!res.ok) {
      bubble.textContent = 'Server response error (' + res.status + ')';
      bubble.classList.add('err');
      return false;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let rawOutput = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\\n');
      buf = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const item = JSON.parse(line);
          if (item.t) {
            rawOutput += item.t;
            bubble.innerHTML = parseMarkdown(rawOutput);
            log.scrollTop = log.scrollHeight;
          } else if (item.error) {
            bubble.textContent = 'Error: ' + item.error;
            bubble.classList.add('err');
          }
        } catch (_) {}
      }
    }

    if (!rawOutput && !bubble.classList.contains('err')) {
      bubble.textContent = 'No reply received from Gemma.';
    }
  } catch (err) {
    bubble.textContent = 'Network communication failure: ' + err.message;
    bubble.classList.add('err');
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
  return false;
}
</script>
</body>
</html>"""


def make_app_server(port=8080, host="127.0.0.1"):
    sessions = {}

    def get_agent(sid):
        if sid not in sessions:
            if len(sessions) >= 50:
                sessions.pop(next(iter(sessions)))
            sessions[sid] = build_agent()
        return sessions[sid]

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, content_type, data):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed_path = urllib.parse.urlsplit(self.path).path
            if parsed_path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            elif parsed_path == "/letterlens.zip":
                data = source_zip()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="letterlens.zip"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed_path == "/sample/notice.md":
                sample_file = os.path.join(ROOT, "samples", "sample_document.md")
                if os.path.isfile(sample_file):
                    with open(sample_file, "rb") as f:
                        data = f.read()
                    self._send(200, "text/markdown; charset=utf-8", data)
                else:
                    self._send(404, "text/plain", b"not found")
            elif parsed_path == "/sample/scan.png":
                sample_img = os.path.join(ROOT, "samples", "sample_letter_scan.png")
                if os.path.isfile(sample_img):
                    with open(sample_img, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    data_url = f"data:image/png;base64,{b64}"
                    self._send(200, "text/plain; charset=utf-8", data_url.encode("utf-8"))
                else:
                    self._send(404, "text/plain", b"not found")
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def do_POST(self):
            parsed_path = urllib.parse.urlsplit(self.path).path
            if parsed_path != "/chat":
                return self._send(404, "text/plain; charset=utf-8", b"not found")

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()

            def _emit(obj):
                self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                self.wfile.flush()

            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                sid = str(body.get("sid", "default"))
                msg = body.get("message", "")
                images = body.get("images") or None
                files = body.get("files") or None

                if images:
                    ocr_results = []
                    for idx, img in enumerate(images):
                        extracted = LocalOCR.extract_text(img)
                        if extracted:
                            ocr_results.append(f"[Document Text Extracted (Image {idx + 1})]:\n\"\"\"\n{extracted}\n\"\"\"")
                    if ocr_results:
                        ocr_block = "\n\n".join(ocr_results)
                        msg = f"{ocr_block}\n\n{msg or 'Explain this document in simple A1-B2 English and cite verified country legal portals with footnotes.'}"

                agent_inst = get_agent(sid)
                agent_inst.run(msg, images=images, files=files, on_token=lambda t: _emit({"t": t}))
            except Exception as e:
                _emit({"error": str(e)})

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv):
    if argv[:1] == ["serve"]:
        host = os.environ.get("LETTERLENS_HOST", "127.0.0.1")
        port = int(os.environ.get("LETTERLENS_PORT", "8080"))
        server = make_app_server(port=port, host=host)
        print(f"http://{host}:{port}")
        server.serve_forever()
        return 0
    if not argv:
        print("Usage: python app.py <file-or-text> | serve")
        return 2

    target = argv[0]
    custom_prompt = argv[1] if len(argv) > 1 else None

    describe(target, prompt=custom_prompt, stream=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
