# -*- coding: utf-8 -*-
"""Korean translation of git commit subjects through a local LLM.

Entirely optional. The report is fully usable without it -- when no server
answers, every subject simply stays in English in both language modes.

Any OpenAI-compatible /v1 endpoint works; the usual local ones are probed in
order (LM Studio, llama.cpp / llama-server, Ollama). Results are cached in
translations.json so the same commit is never paid for twice.
"""
import io
import json
import os
import re
import time
from urllib import request as urlrequest

DEFAULT_ENDPOINTS = (
    "http://127.0.0.1:1234/v1",   # LM Studio
    "http://127.0.0.1:8080/v1",   # llama.cpp llama-server / router
    "http://127.0.0.1:11434/v1",  # Ollama
)
DISCOVER_TIMEOUT = 2.0      # a closed port refuses instantly; this covers slow starts
REQUEST_TIMEOUT = 90.0      # one batch
BUDGET = 180.0              # whole run; whatever is left over stays English
BATCH = 12
CACHE_VERSION = 1

SYSTEM_PROMPT = (
    "You translate git commit subjects into natural Korean for a ComfyUI "
    "changelog.\n"
    "Rules:\n"
    "- Keep code identifiers, file paths, CLI flags, model/node/package names, "
    "numbers and versions exactly as they are, in English.\n"
    "- Keep it short and factual, like a changelog line. No trailing period.\n"
    "- Translate a conventional-commit prefix only when the input has one "
    "(fix: -> '수정:', feat: -> '추가:', chore: -> '정리:'). Never add a prefix "
    "that is not in the input.\n"
    "- These are software changes: translate 'editor' as 편집기, 'handler' as "
    "처리기, and so on -- never as a person.\n"
    "- Reply with ONLY a JSON array of strings, one per input line, in the "
    "same order and the same length. No commentary, no code fence."
)

_FENCE_RE = re.compile(r"^\s*[`]{3}[a-zA-Z]*\s*|\s*[`]{3}\s*$")
_NUMBERED_RE = re.compile(r"^\s*(\d+)\s*[.)\]]\s*(.+?)\s*$")
_HANGUL_RE = re.compile(r"[가-힣]")
_LATIN_RE = re.compile(r"[A-Za-z]")


# ------------------------------------------------------------------ http --

def http_json(url, payload=None, timeout=DISCOVER_TIMEOUT, key=None):
    """GET (payload=None) or POST JSON, returning the decoded response."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urlrequest.Request(url, data=data, headers=headers)
    response = urlrequest.urlopen(req, timeout=timeout)
    try:
        return json.loads(response.read().decode("utf-8", "replace"))
    finally:
        response.close()


def status_of(error):
    """The HTTP status behind a failed request, if it had one."""
    return getattr(error, "status", None) or getattr(error, "code", None)


def key_from_env(env=None):
    """An API key from the usual environment variables, or None."""
    env = os.environ if env is None else env
    for name in ("COMFY_REPORT_LLM_KEY", "LM_API_TOKEN", "OPENAI_API_KEY"):
        value = (env.get(name) or "").strip()
        if value:
            return value
    return None


# ------------------------------------------------------------- discovery --

def discover(endpoints=None, model=None, http=http_json, key=None, notes=None):
    """First endpoint that answers /models, as (base_url, model_id) or (None, None).

    A server that answers 401/403 is skipped like any other, but the reason is
    recorded in `notes` so the caller can say so instead of staying silent.
    """
    for base in endpoints or DEFAULT_ENDPOINTS:
        base = base.rstrip("/")
        try:
            body = http(base + "/models", None, DISCOVER_TIMEOUT, key)
        except Exception as error:
            if status_of(error) in (401, 403) and notes is not None:
                notes.append("auth")
            continue
        ids = [m.get("id") for m in (body or {}).get("data") or [] if m.get("id")]
        if model:
            return base, model
        if ids:
            return base, ids[0]
    return None, None


# ----------------------------------------------------------------- parse --

def parse_reply(text, expected):
    """The answer as a list of `expected` strings, or None.

    Accepts a bare JSON array, a fenced one, and the numbered list small
    models like to produce anyway.
    """
    if not text:
        return None
    text = _FENCE_RE.sub("", text.strip())

    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            items = json.loads(text[start:end + 1])
        except ValueError:
            items = None
        if (isinstance(items, list) and len(items) == expected
                and all(isinstance(i, str) for i in items)):
            return [i.strip() for i in items]

    numbered = {}
    for line in text.splitlines():
        match = _NUMBERED_RE.match(line)
        if match:
            numbered[int(match.group(1))] = match.group(2).strip().strip('"')
    if len(numbered) == expected and set(numbered) == set(range(1, expected + 1)):
        return [numbered[i] for i in range(1, expected + 1)]
    return None


def needs_translation(subject):
    """Skip what a translation cannot improve: empty, already Korean, no words."""
    if not subject or not subject.strip():
        return False
    if _HANGUL_RE.search(subject):
        return False
    return bool(_LATIN_RE.search(subject))


# ----------------------------------------------------------------- cache --

def load_cache(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError):
        return {}
    if isinstance(body, dict) and body.get("version") == CACHE_VERSION:
        entries = body.get("entries")
        if isinstance(entries, dict):
            return {k: v for k, v in entries.items() if isinstance(v, str)}
    return {}


def save_cache(path, entries):
    tmp = path + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"version": CACHE_VERSION, "entries": entries},
                      fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


# ------------------------------------------------------------ translator --

class Translator(object):
    """English commit subjects -> Korean: cached, budgeted, and never fatal."""

    def __init__(self, cache_path=None, endpoints=None, model=None, key=None,
                 batch=BATCH, budget=BUDGET, timeout=REQUEST_TIMEOUT,
                 http=http_json, clock=time.time):
        self.cache_path = cache_path
        self.endpoints = endpoints
        self.model = model
        self.key = key
        self.batch = max(1, batch)
        self.budget = budget
        self.timeout = timeout
        self.http = http
        self.clock = clock
        self.entries = load_cache(cache_path) if cache_path else {}
        self.base = None
        self.used_model = None
        self.translated = 0
        self.needs_key = False

    def _ask(self, subjects):
        payload = {
            "model": self.used_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",
                 "content": json.dumps(subjects, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        body = self.http(self.base + "/chat/completions", payload, self.timeout,
                         self.key)
        choices = (body or {}).get("choices") or []
        if not choices:
            return None
        content = (choices[0].get("message") or {}).get("content")
        return parse_reply(content, len(subjects))

    def pending(self, subjects):
        """The subjects worth asking about that are not cached yet."""
        wanted, seen = [], set()
        for subject in subjects:
            if subject in seen or not needs_translation(subject):
                continue
            seen.add(subject)
            wanted.append(subject)
        return wanted

    def translate(self, subjects):
        """{english: korean} for whatever could be translated.

        Cached entries come back for free. Anything the LLM cannot be reached
        for, or answers badly, is simply absent from the result.
        """
        wanted = self.pending(subjects)
        done = {s: self.entries[s] for s in wanted if s in self.entries}
        missing = [s for s in wanted if s not in self.entries]
        if not missing:
            return done

        notes = []
        self.base, self.used_model = discover(self.endpoints, self.model, self.http,
                                              self.key, notes)
        if not self.base:
            self.needs_key = "auth" in notes
            return done

        deadline = self.clock() + self.budget
        dirty = False
        for start in range(0, len(missing), self.batch):
            if self.clock() >= deadline:
                break
            chunk = missing[start:start + self.batch]
            try:
                answers = self._ask(chunk)
            except Exception:
                break
            if not answers:
                continue
            for english, korean in zip(chunk, answers):
                if korean and korean != english:
                    self.entries[english] = korean
                    done[english] = korean
                    dirty = True
                    self.translated += 1

        if dirty and self.cache_path:
            save_cache(self.cache_path, self.entries)
        return done


def commit_subjects(d):
    """Every commit dict in a diff, core first."""
    commits = []
    for entry in ([d["core"]] if d.get("core") else []) + (d.get("nodes_changed") or []):
        commits.extend(entry.get("commits") or [])
    return commits


def apply_to_diff(d, translator):
    """Attach `subject_ko` to every commit the translator could handle."""
    commits = commit_subjects(d)
    if not commits:
        return 0
    table = translator.translate([c.get("subject", "") for c in commits])
    for commit in commits:
        korean = table.get(commit.get("subject", ""))
        if korean:
            commit["subject_ko"] = korean
    return translator.translated
