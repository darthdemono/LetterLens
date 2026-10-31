import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Union

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

MODEL = os.environ.get("LETTERLENS_MODEL", "gemma-4-26b-a4b-it")
URL = os.environ.get("LETTERLENS_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")

SYSTEM_PROMPT = (
    "You are LetterLens Agent, an AI document assistant. You explain official letters, "
    "government notices, contracts, bills, and residency documents in plain English. "
    "Your readers understand English at an A1 to B2 level. "
    "Write in simple, clear, and direct English. "
    "Do not use difficult legal terms, Latin phrases, or complex bureaucratic words. "
    "If an official term is needed, explain what it means in simple words. "
    "Use short sentences and clear bullet points. "
    "Do not use any emojis. "
    "Structure every reply with these Markdown sections:\n"
    "### Summary\n"
    "What this document is and who sent it in 1 or 2 simple sentences.\n"
    "### What You Must Do\n"
    "Clear, numbered steps of the exact actions required.\n"
    "### Deadlines\n"
    "Important dates, time limits, or number of days you have to act.\n"
    "### Money and Fees\n"
    "Any costs, price increases, fee amounts, or penalties.\n"
    "### What Happens If You Do Nothing\n"
    "The consequences if you ignore this letter.\n"
    "### Summary Table\n"
    "A simple Markdown table comparing options or summarizing key details.\n\n"
    "Always end with a short notice: Disclaimer: This is a plain language explanation, not formal legal advice."
)

THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MARKDOWN_EXTS = {".md", ".markdown"}
TEXT_EXTS = {".txt", ".rst", ".json", ".csv", ".yaml", ".yml", ".html", ".log"}


class KeyPool:
    def __init__(self, keys: Optional[Union[str, List[str]]] = None):
        self._lock = threading.Lock()
        self._idx = 0
        self._keys: List[str] = []
        if keys:
            if isinstance(keys, str):
                self._keys = [k.strip() for k in keys.split(",") if k.strip()]
            else:
                self._keys = [k.strip() for k in keys if k and k.strip()]
        if not self._keys:
            self._discover_keys()

    def _discover_keys(self) -> None:
        found: List[str] = []
        known_vars = ("GEMINI_1_KEY", "GEMINI_2_KEY", "GEMINI_3_KEY", "GEMINI_KEYS", "GEMINI_KEY", "GEMINI_API_KEY", "TINYHARNESS_API_KEY", "TINYHARNESS_API_KEYS")
        for var in known_vars:
            val = os.environ.get(var, "")
            for part in val.split(","):
                part = part.strip()
                if part and part not in found:
                    found.append(part)

        items = []
        for k, v in os.environ.items():
            k_upper = k.upper()
            if ("GEMINI" in k_upper and "KEY" in k_upper) or ("TINYHARNESS" in k_upper and "KEY" in k_upper):
                val = v.strip()
                if val:
                    items.append((k, val))

        items.sort(key=lambda x: x[0])
        for _, val in items:
            for part in val.split(","):
                part = part.strip()
                if part and part not in found:
                    found.append(part)

        self._keys = found
        if self._keys and not os.environ.get("TINYHARNESS_API_KEY"):
            os.environ["TINYHARNESS_API_KEY"] = self._keys[0]

    def get_key(self) -> str:
        with self._lock:
            if not self._keys:
                return os.environ.get("TINYHARNESS_API_KEY", "")
            key = self._keys[self._idx % len(self._keys)]
            self._idx = (self._idx + 1) % len(self._keys)
            return key

    def get_all(self) -> List[str]:
        return list(self._keys)

    def size(self) -> int:
        return len(self._keys)


GLOBAL_KEY_POOL = KeyPool()


class LocalOCR:
    _win_ps_script_path = None

    @classmethod
    def _get_win_ps_script(cls) -> str:
        if cls._win_ps_script_path and os.path.isfile(cls._win_ps_script_path):
            return cls._win_ps_script_path

        script_content = r"""param([string]$ImagePath)
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }

    function Await($WinRtTask, $ResultType) {
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $netTask.Wait()
        return $netTask.Result
    }

    [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType=WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation.UniversalApiContract, ContentType=WindowsRuntime] | Out-Null

    $fullPath = [System.IO.Path]::GetFullPath($ImagePath)
    $fileTask = [Windows.Storage.StorageFile]::GetFileFromPathAsync($fullPath)
    $file = Await $fileTask ([Windows.Storage.StorageFile])

    $streamTask = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    $stream = Await $streamTask ([Windows.Storage.Streams.IRandomAccessStream])

    $decoderTask = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $decoder = Await $decoderTask ([Windows.Graphics.Imaging.BitmapDecoder])

    $bitmapTask = $decoder.GetSoftwareBitmapAsync()
    $bitmap = Await $bitmapTask ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language('en-US')))
    }

    $ocrTask = $engine.RecognizeAsync($bitmap)
    $ocrResult = Await $ocrTask ([Windows.Media.Ocr.OcrResult])
    Write-Output $ocrResult.Text
} catch {
    Write-Output ""
}
"""
        temp_dir = tempfile.gettempdir()
        script_path = os.path.join(temp_dir, "letterlens_win_ocr.ps1")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        cls._win_ps_script_path = script_path
        return script_path

    @classmethod
    def extract_text(cls, image_path_or_data: Union[str, bytes], timeout: float = 6.0) -> str:
        temp_file = None
        try:
            if isinstance(image_path_or_data, bytes):
                temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                temp_file.write(image_path_or_data)
                temp_file.close()
                target_path = temp_file.name
            elif isinstance(image_path_or_data, str):
                if image_path_or_data.startswith("data:"):
                    _, b64 = image_path_or_data.split(",", 1)
                    raw_bytes = base64.b64decode(b64)
                    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    temp_file.write(raw_bytes)
                    temp_file.close()
                    target_path = temp_file.name
                elif os.path.isfile(image_path_or_data):
                    target_path = os.path.abspath(image_path_or_data)
                else:
                    return ""
            else:
                return ""

            if sys.platform.startswith("win"):
                try:
                    ps_script = cls._get_win_ps_script()
                    cmd = [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        ps_script,
                        target_path,
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                    text = res.stdout.strip()
                    if text:
                        return text
                except Exception:
                    pass

            try:
                import pytesseract
                from PIL import Image
                img = Image.open(target_path)
                text = pytesseract.image_to_string(img).strip()
                if text:
                    return text
            except Exception:
                pass

            return ""
        finally:
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except Exception:
                    pass


class MarkdownParser:
    @staticmethod
    def parse_sections(md_text: str) -> Dict[str, str]:
        sections = {}
        current_heading = "Introduction"
        current_lines = []

        for line in md_text.splitlines():
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                if current_lines:
                    sections[current_heading] = "\n".join(current_lines).strip()
                    current_lines = []
                current_heading = m.group(2).strip()
            else:
                current_lines.append(line)

        if current_lines:
            sections[current_heading] = "\n".join(current_lines).strip()

        return sections

    @staticmethod
    def extract_key_fields(md_text: str) -> Dict[str, Any]:
        fields: Dict[str, Any] = {
            "deadlines": [],
            "amounts": [],
            "contacts": [],
            "actions": [],
        }

        date_patterns = [
            r"\b\d{4}-\d{2}-\d{2}\b",
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s+\d{4})?\b",
            r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
            r"\bwithin\s+\d+\s+days\b",
        ]
        for pat in date_patterns:
            matches = re.findall(pat, md_text, flags=re.IGNORECASE)
            for match in matches:
                if match not in fields["deadlines"]:
                    fields["deadlines"].append(match)

        amount_pat = r"\b\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?\s*(?:HUF|EUR|USD|GBP|Ft|€|\$|£)\b"
        amounts = re.findall(amount_pat, md_text, flags=re.IGNORECASE)
        for a in amounts:
            if a not in fields["amounts"]:
                fields["amounts"].append(a)

        emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", md_text)
        fields["contacts"].extend(list(set(emails)))

        for line in md_text.splitlines():
            s = line.strip()
            if s.startswith("- [ ]") or s.startswith("* [ ]"):
                fields["actions"].append(s[5:].strip())
            elif re.match(r"^\d+\.\s+\*\*(.*?)\*\*", s):
                fields["actions"].append(s)

        return fields

    @staticmethod
    def to_terminal_text(md_text: str, use_color: bool = True) -> str:
        if not use_color or not sys.stdout.isatty():
            clean = md_text
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", clean)
            clean = re.sub(r"\*(.*?)\*", r"\1", clean)
            clean = re.sub(r"^#{1,6}\s+", "", clean, flags=re.MULTILINE)
            return clean

        BOLD = "\033[1m"
        BLUE = "\033[38;5;75m"
        CYAN = "\033[38;5;80m"
        GRAY = "\033[38;5;244m"
        RESET = "\033[0m"

        out_lines = []
        in_table = False
        table_lines = []

        def flush_table():
            nonlocal in_table, table_lines
            if not table_lines:
                return
            rows = []
            for t_line in table_lines:
                cells = [c.strip() for c in t_line.strip().strip("|").split("|")]
                if all(re.match(r"^:?-+:?$", c) for c in cells):
                    continue
                rows.append(cells)
            if rows:
                col_widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(len(rows[0]))]
                header = rows[0]
                h_str = " | ".join(header[i].ljust(col_widths[i]) if i < len(header) else "" for i in range(len(col_widths)))
                sep = "-+-".join("-" * w for w in col_widths)
                out_lines.append(f"  {BOLD}{CYAN}{h_str}{RESET}")
                out_lines.append(f"  {GRAY}{sep}{RESET}")
                for row in rows[1:]:
                    r_str = " | ".join(row[i].ljust(col_widths[i]) if i < len(row) else "" for i in range(len(col_widths)))
                    out_lines.append(f"  {r_str}")
            table_lines = []
            in_table = False

        for line in md_text.splitlines():
            if "|" in line and (line.strip().startswith("|") or line.strip().endswith("|")):
                in_table = True
                table_lines.append(line)
                continue
            elif in_table:
                flush_table()

            m_h = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m_h:
                level = len(m_h.group(1))
                title = m_h.group(2).strip()
                prefix = "#" * level
                out_lines.append(f"\n{BOLD}{BLUE}{prefix} {title}{RESET}")
                continue

            if re.match(r"^(\*{3,}|-{3,}|_{3,})$", line.strip()):
                out_lines.append(f"{GRAY}{'-' * 60}{RESET}")
                continue

            m_b = re.match(r"^(\s*)[*+-]\s+(.*)$", line)
            if m_b:
                indent = m_b.group(1)
                text = m_b.group(2)
                text = re.sub(r"\*\*(.*?)\*\*", f"{BOLD}\\1{RESET}", text)
                out_lines.append(f"{indent}  * {text}")
                continue

            styled = re.sub(r"\*\*(.*?)\*\*", f"{BOLD}\\1{RESET}", line)
            styled = re.sub(r"\*(.*?)\*", f"\033[3m\\1{RESET}", styled)
            out_lines.append(styled)

        if in_table:
            flush_table()

        return "\n".join(out_lines)


class Client:
    def __init__(self, model=None, base_url=URL, api_key=None, key_pool=None, timeout=120):
        self.model = model or MODEL
        self.base_url = (base_url or URL).rstrip("/")
        if api_key:
            self.key_pool = KeyPool([api_key])
        elif key_pool:
            self.key_pool = key_pool
        else:
            self.key_pool = GLOBAL_KEY_POOL
        self.api_key = api_key or (self.key_pool.get_all()[0] if self.key_pool.size() > 0 else "")
        self.timeout = timeout

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, on_token: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools

        active_key = self.key_pool.get_key() if self.key_pool.size() > 0 else self.api_key
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {active_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        msg = data["choices"][0]["message"]
        if on_token and msg.get("content"):
            on_token(msg["content"])
        return msg


class GeminiClient(Client):
    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, on_token: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        reply = None
        max_attempts = max(4, self.key_pool.size() * 2) if hasattr(self, "key_pool") else 4
        for attempt in range(max_attempts):
            try:
                reply = super().chat(messages, tools)
                break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == (max_attempts - 1):
                    raise
                time.sleep(min(2**attempt, 4))

        content = reply.get("content") or ""
        cleaned = THOUGHT_RE.sub("", content).strip()
        reply["content"] = cleaned

        if on_token and cleaned:
            on_token(cleaned)
        return reply


class GemmaAgent:
    def __init__(self, client: Optional[Client] = None, system: str = SYSTEM_PROMPT, max_steps: int = 6):
        self.client = client or GeminiClient()
        self.system = system
        self.max_steps = max_steps
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]

    def add_tools(self, tools_list: List[Dict[str, Any]]) -> None:
        for t in tools_list:
            if isinstance(t, dict):
                self.tools[t["name"]] = t

    def _tools_schema(self) -> Optional[List[Dict[str, Any]]]:
        if not self.tools:
            return None
        return [t["schema"] for t in self.tools.values()]

    def describe(
        self,
        target: Union[str, bytes],
        prompt: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> str:
        if isinstance(target, bytes) or (isinstance(target, str) and self._is_image_target(target)):
            ocr_text = LocalOCR.extract_text(target)
            custom_prompt = prompt or "Explain this official letter or document in simple A1-B2 English:"
            if ocr_text:
                full_prompt = (
                    f"[Extracted Document Text]:\n\"\"\"\n{ocr_text}\n\"\"\"\n\n"
                    f"{custom_prompt}\n"
                    "Confirm the numbers, dates, and names from the document image."
                )
            else:
                full_prompt = custom_prompt

            return self.run(prompt=full_prompt, images=[target], on_token=on_token)

        if isinstance(target, str) and os.path.isfile(target):
            _, ext = os.path.splitext(target.lower())
            if ext in MARKDOWN_EXTS:
                with open(target, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                filename = os.path.basename(target)
                doc_prompt = (
                    f"--- Attached Document: {filename} ---\n{content}\n\n"
                    f"{prompt or 'Explain this document in simple A1-B2 English:'}"
                )
                return self.run(prompt=doc_prompt, on_token=on_token)

            if ext in TEXT_EXTS:
                with open(target, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                filename = os.path.basename(target)
                doc_prompt = (
                    f"--- Attached Document: {filename} ---\n{content}\n\n"
                    f"{prompt or 'Explain this document in simple A1-B2 English:'}"
                )
                return self.run(prompt=doc_prompt, on_token=on_token)

        raw_text = str(target)
        if prompt:
            full_prompt = f"{prompt}\n\n[Document Text]:\n{raw_text}"
        else:
            full_prompt = raw_text

        return self.run(prompt=full_prompt, on_token=on_token)

    def run(
        self,
        prompt: str,
        images: Optional[List[Any]] = None,
        files: Optional[List[Dict[str, str]]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> str:
        prompt_text = prompt or "Explain this document in simple A1-B2 English:"

        if files:
            file_sections = []
            for item in files:
                fname = item.get("name", "document")
                fcontent = item.get("content", "")
                file_sections.append(f"--- Document: {fname} ---\n{fcontent}")
            prompt_text = "\n\n".join(file_sections) + f"\n\n{prompt_text}"

        if images:
            content_parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]
            for img in images:
                if isinstance(img, bytes):
                    b64 = base64.b64encode(img).decode("utf-8")
                    data_url = f"data:image/png;base64,{b64}"
                elif isinstance(img, str) and os.path.isfile(img):
                    mime, _ = mimetypes.guess_type(img)
                    mime = mime or "image/png"
                    with open(img, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    data_url = f"data:{mime};base64,{b64}"
                elif str(img).startswith("data:"):
                    data_url = str(img)
                else:
                    data_url = f"data:image/png;base64,{img}"
                content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
            user_msg = {"role": "user", "content": content_parts}
        else:
            user_msg = {"role": "user", "content": prompt_text}

        self.messages.append(user_msg)

        for _ in range(self.max_steps):
            schema = self._tools_schema()
            msg = self.client.chat(self.messages, tools=schema, on_token=on_token)
            calls = msg.get("tool_calls")
            self.messages.append(msg)
            if not calls:
                return msg.get("content", "")
            for c in calls:
                fn_name = c["function"]["name"]
                fn_args = c["function"]["arguments"]
                if isinstance(fn_args, str):
                    fn_args = json.loads(fn_args or "{}")
                if fn_name in self.tools:
                    res = str(self.tools[fn_name]["fn"](**fn_args))
                else:
                    res = f"error: unknown tool {fn_name}"
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", fn_name),
                    "name": fn_name,
                    "content": res,
                })

        return msg.get("content", "")

    @staticmethod
    def _is_image_target(target: str) -> bool:
        if target.startswith("data:image/"):
            return True
        if os.path.isfile(target):
            _, ext = os.path.splitext(target.lower())
            return ext in IMAGE_EXTS
        return False


def build_agent(client: Optional[Client] = None) -> GemmaAgent:
    agent = GemmaAgent(client or GeminiClient(), system=SYSTEM_PROMPT)
    rules_dir = os.environ.get("LETTERLENS_RULES")
    if rules_dir and os.path.isdir(rules_dir):
        import app
        if hasattr(app, "folder_tools"):
            agent.add_tools(app.folder_tools(rules_dir))
    return agent


def describe(
    target: Union[str, bytes],
    prompt: Optional[str] = None,
    client: Optional[Client] = None,
    stream: bool = True,
) -> str:
    agent = build_agent(client)
    cb = (lambda t: print(t, end="", flush=True)) if stream else None
    return agent.describe(target, prompt=prompt, on_token=cb)


def interactive_shell():
    agent = build_agent()
    print("=================================================================")
    print("  LetterLens Gemma 4 Agent")
    print("  Enter picture path (.png, .jpg), markdown (.md), or text.")
    print("  Type 'exit' to quit.")
    print("=================================================================\n")

    while True:
        try:
            line = input("\nLetterLens Agent > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSession ended.")
            break

        if not line:
            continue
        if line.lower() in {"exit", "quit", "q"}:
            print("Session ended.")
            break

        try:
            agent.describe(line, on_token=lambda t: print(t, end="", flush=True))
            print()
        except Exception as e:
            print(f"Error: {e}")


def main(argv: List[str]) -> int:
    if not argv or argv == ["--interactive"] or argv == ["-i"]:
        interactive_shell()
        return 0

    target = argv[0]
    prompt = argv[1] if len(argv) > 1 else None

    agent = build_agent()
    try:
        agent.describe(target, prompt=prompt, on_token=lambda t: print(t, end="", flush=True))
        print()
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
