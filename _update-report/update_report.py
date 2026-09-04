# -*- coding: utf-8 -*-
"""Show what actually changed the last time ComfyUI was updated.

Keeps a snapshot of (core commit, per-node revision, installed packages) in
state.json and diffs the current install against it. Nothing needs to run
*before* an update -- the previous report's snapshot is the baseline.
"""
import html
import io
import json
import os
import re
import subprocess
import sys
import webbrowser
from concurrent import futures
from datetime import datetime
from importlib import metadata

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

# An embedded python (._pth) may not put the script directory on sys.path,
# and the report is launched by absolute path from the update .bat files.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n          # noqa: E402  (must follow the sys.path fix)
import translate     # noqa: E402

__version__ = "1.2.0"

# Packages whose version moving under you tends to break ComfyUI outright.
RISKY_PACKAGES = {
    "av", "numpy", "scipy", "numba", "llvmlite",
    "torch", "torchvision", "torchaudio", "torchsde", "xformers",
    "opencv-python", "opencv-python-headless", "opencv-contrib-python",
    "transformers", "diffusers", "safetensors", "pillow", "torchcodec",
}

_VERSION_RE = re.compile(r"""^\s*version\s*=\s*["']([^"']+)""", re.MULTILINE)
_GITHUB_RE = re.compile(r"^(?:https?://|git@)github\.com[:/](?P<slug>[^/]+/[^/]+?)(?:\.git)?/?$")
_SKIP_DIRS = {"__pycache__"}
COMMIT_LIMIT = 20


# ------------------------------------------------------------------- diff --

def _rev(node):
    """A git node's revision is its SHA; a registry node's is its version."""
    return node.get("sha") if node.get("kind") == "git" else node.get("version")


def _sort_key(entry):
    return entry["name"].lower()


def diff_state(old, new):
    old_nodes, new_nodes = old.get("nodes", {}), new.get("nodes", {})
    old_pkgs, new_pkgs = old.get("packages", {}), new.get("packages", {})

    old_core = (old.get("core") or {}).get("sha")
    new_core = (new.get("core") or {}).get("sha")
    core = None
    if old_core != new_core:
        core = {"old": old_core, "new": new_core,
                "remote": (new.get("core") or {}).get("remote")}

    changed, added, removed = [], [], []
    for name, node in new_nodes.items():
        if name not in old_nodes:
            added.append({"name": name, "kind": node.get("kind"),
                          "new": _rev(node), "remote": node.get("remote")})
        elif _rev(old_nodes[name]) != _rev(node):
            changed.append({"name": name, "kind": node.get("kind"),
                            "old": _rev(old_nodes[name]), "new": _rev(node),
                            "remote": node.get("remote")})
    for name, node in old_nodes.items():
        if name not in new_nodes:
            removed.append({"name": name, "kind": node.get("kind"), "old": _rev(node)})

    pkg_changed, pkg_added, pkg_removed = [], [], []
    for name, version in new_pkgs.items():
        if name not in old_pkgs:
            pkg_added.append({"name": name, "new": version})
        elif old_pkgs[name] != version:
            pkg_changed.append({"name": name, "old": old_pkgs[name], "new": version,
                                "risky": name.lower() in RISKY_PACKAGES})
    for name, version in old_pkgs.items():
        if name not in new_pkgs:
            pkg_removed.append({"name": name, "old": version})

    for group in (changed, added, removed, pkg_changed, pkg_added, pkg_removed):
        group.sort(key=_sort_key)

    return {
        "core": core,
        "nodes_changed": changed,
        "nodes_added": added,
        "nodes_removed": removed,
        "packages_changed": pkg_changed,
        "packages_added": pkg_added,
        "packages_removed": pkg_removed,
        "has_changes": bool(core or changed or added or removed
                            or pkg_changed or pkg_added or pkg_removed),
    }


# ---------------------------------------------------------------- parsing --

def parse_pyproject_version(text):
    """The [project] version of a pyproject.toml, falling back to a regex."""
    if not text:
        return None
    if tomllib is not None:
        try:
            return tomllib.loads(text).get("project", {}).get("version")
        except (tomllib.TOMLDecodeError, ValueError):
            pass
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def parse_git_log(text):
    """Parse `git log --pretty=%h%x09%as%x09%s` output into commit dicts."""
    commits = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


def compare_url(remote, old, new):
    """A GitHub compare link, or None for any other host."""
    if not remote:
        return None
    match = _GITHUB_RE.match(remote.strip())
    if not match:
        return None
    return "https://github.com/{}/compare/{}...{}".format(match.group("slug"), old, new)


# -------------------------------------------------------------- rendering --

_ANSI = {"g": "\033[92m", "y": "\033[93m", "r": "\033[91m", "d": "\033[90m", "0": "\033[0m"}
LABEL_COLUMNS = 12


def _c(code, text, color):
    return _ANSI[code] + text + _ANSI["0"] if color else text


def _short(sha):
    return (sha or "?")[:8]


def render_console(d, report_path=None, generated_at=None, color=False,
                   lang=i18n.DEFAULT_LANG, translated=0, model=None,
                   needs_key=False, engine=None, unreachable=False):
    """A few lines for the update window, in one language."""
    lang = i18n.normalize(lang)

    def s(key, **fmt):
        return i18n.t(key, lang, **fmt)

    def row(label, body):
        return "  " + i18n.pad(label, LABEL_COLUMNS) + body

    title = s("title")
    if generated_at:
        title += " (" + generated_at + ")"
    lines = ["", _c("g", "::::::::::: " + title + " :::::::::::", color)]

    if not d["has_changes"]:
        lines += ["  " + _c("d", s("no_changes"), color), ""]
        return "\n".join(lines)

    core = d["core"]
    if core:
        n = len(core.get("commits") or [])
        count = s("c_commits", n=n) if n else s("c_commits_none")
        lines.append(row(s("c_core"), "{} -> {}  ({})".format(
            _short(core["old"]), _c("y", _short(core["new"]), color), count)))

    if d["nodes_changed"] or d["nodes_added"] or d["nodes_removed"]:
        lines.append(row(s("c_nodes"), s("c_node_counts",
                                         changed=len(d["nodes_changed"]),
                                         added=len(d["nodes_added"]),
                                         removed=len(d["nodes_removed"]))))

    pkg_total = (len(d["packages_changed"]) + len(d["packages_added"])
                 + len(d["packages_removed"]))
    if pkg_total:
        line = row(s("c_packages"), s("c_pkg_counts", n=pkg_total))
        risky = [p for p in d["packages_changed"] if p.get("risky")]
        if risky:
            detail = ", ".join("{} {} -> {}".format(p["name"], p["old"], p["new"])
                               for p in risky)
            line += "  " + _c("r", "[!] " + detail, color)
        lines.append(line)

    if translated:
        label = s("engine_google") if engine == "google" else (model or "?")
        lines.append(row(s("c_translate_label"),
                         _c("d", s("c_translated", n=translated, model=label), color)))
    elif needs_key:
        lines.append(row(s("c_translate_label"), _c("d", s("c_llm_auth"), color)))
    elif unreachable and engine == "google":
        lines.append(row(s("c_translate_label"), _c("d", s("c_google_down"), color)))
    if report_path:
        lines.append(row(s("c_report"), str(report_path)))
    lines.append("")
    return "\n".join(lines)


def _esc(text):
    return html.escape(str(text) if text is not None else "")


def _bi(ko, en):
    """Ship both languages; the browser hides one. Identical text stays plain."""
    ko_text, en_text = _esc(ko), _esc(en)
    if ko_text == en_text:
        return ko_text
    return ('<span class="lg-ko">{}</span>'
            '<span class="lg-en">{}</span>').format(ko_text, en_text)


def _s(key, **fmt):
    """A table string in both languages."""
    return _bi(*i18n.both(key, **fmt))


def _commit_rows(commits, extra_count=0):
    if not commits:
        return '<p class="none">' + _s("commits_none") + "</p>"
    rows = "".join(
        '<tr><td class="date">{}</td><td class="sha">{}</td><td>{}</td></tr>'.format(
            _esc(c["date"]), _esc(c["sha"]),
            _bi(c.get("subject_ko") or c.get("subject"), c.get("subject")))
        for c in commits)
    more = ('<tr><td></td><td></td><td class="none">{}</td></tr>'.format(
        _s("more_commits", n=extra_count)) if extra_count else "")
    return '<table class="commits">' + rows + more + "</table>"


def _entry_block(entry, kind_label):
    name = _esc(entry["name"])
    remote = entry.get("remote")
    head = ('<a href="{}" target="_blank" rel="noreferrer">{}</a>'.format(_esc(remote), name)
            if remote else name)

    old, new = entry.get("old"), entry.get("new")
    if entry.get("kind") == "git" and old and new:
        transition = "{}..{}".format(_short(old), _short(new))
        link = compare_url(remote, _short(old), _short(new))
        if link:
            transition = '<a href="{}" target="_blank" rel="noreferrer">{}</a>'.format(
                _esc(link), transition)
    elif old and new:
        transition = "{} &rarr; {}".format(_esc(old), _esc(new))
    else:
        transition = _esc(new or old or "")

    body = ""
    if "commits" in entry:
        body = _commit_rows(entry.get("commits"), entry.get("commits_omitted", 0))
    return ('<section class="entry"><h3><span class="tag {}">{}</span>{}'
            '<span class="rev">{}</span></h3>{}</section>').format(
        _esc(entry.get("kind") or "cnr"), kind_label, head, transition, body)


_CSS = """
:root{color-scheme:dark}
body{background:#14161a;color:#dde2e8;font:14px/1.6 "Segoe UI",Malgun Gothic,sans-serif;
     margin:0;padding:32px 28px 64px;max-width:1100px}
h1{font-size:20px;margin:0 0 4px}
.meta{color:#7c8794;font-size:12px}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;
     flex-wrap:wrap;margin-bottom:24px}
.langbar{display:flex;border:1px solid #2b3038;border-radius:999px;overflow:hidden;
         background:#1b1e24;flex:none}
.langbar button{appearance:none;-webkit-appearance:none;border:0;background:transparent;
                color:#8a94a0;font:12px/1 inherit;padding:8px 14px;cursor:pointer}
.langbar button.on{background:#2b3752;color:#cfe0f3}
.langbar button:hover{color:#dde2e8}
.langbar button:focus-visible{outline:2px solid #79a6d2;outline-offset:-2px}
.hint{color:#5f6874;margin-left:8px}
.mtnote{color:#5f6874;font-size:11px;margin:0 0 20px}
html[data-lang="ko"] .lg-en{display:none}
html[data-lang="en"] .lg-ko{display:none}
h2{font-size:15px;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #2b3038;
   color:#9fd39f}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.card{background:#1b1e24;border:1px solid #2b3038;border-radius:8px;padding:10px 14px;
      min-width:120px}
.card b{display:block;font-size:20px;font-weight:600}
.card span{color:#7c8794;font-size:12px}
.card.warn{border-color:#7a4a20;background:#241c14}
.card.warn b{color:#ffb454}
.entry{background:#1b1e24;border:1px solid #2b3038;border-radius:8px;padding:12px 14px;
       margin-bottom:10px}
.entry h3{font-size:14px;margin:0;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.entry h3 a{color:#dde2e8}
.tag{font-size:10px;padding:2px 6px;border-radius:4px;background:#2b3038;color:#8fa3b8;
     text-transform:uppercase;letter-spacing:.5px}
.tag.cnr{background:#243040;color:#79a6d2}
.rev{margin-left:auto;font-family:Consolas,monospace;font-size:12px;color:#8a94a0}
.rev a{color:#79a6d2}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
.commits td{padding:3px 8px 3px 0;vertical-align:top;border-top:1px solid #22262d}
.commits tr:first-child td{border-top:0}
td.date{color:#6f7985;white-space:nowrap;width:92px;font-family:Consolas,monospace}
td.sha{color:#c9a45c;white-space:nowrap;width:80px;font-family:Consolas,monospace}
.pkgs td{padding:4px 10px 4px 0;border-top:1px solid #22262d;font-family:Consolas,monospace}
.pkg.risky td{background:#241c14;color:#ffb454}
.pkg.risky td:first-child::before{content:"[!] "}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#1b1e24;border:1px solid #2b3038;border-radius:6px;padding:4px 9px;font-size:12px}
.none{color:#6f7985;font-size:12px;margin:4px 0}
a{color:#79a6d2}
@media (max-width:640px){body{padding:20px 14px 48px}.rev{margin-left:0;width:100%}}
"""

# Restores the reader's choice before the first paint, so switching to English
# once sticks for every later report too.
_BOOT_JS = """
try{var l=localStorage.getItem('comfy-report-lang');
if(l==='ko'||l==='en'){var r=document.documentElement;
r.setAttribute('data-lang',l);r.setAttribute('lang',l);}}catch(e){}
"""

_TOGGLE_JS = """
(function(){
var root=document.documentElement;
var buttons=document.querySelectorAll('.langbtn');
function set(lang){
  root.setAttribute('data-lang',lang);
  root.setAttribute('lang',lang);
  var title=root.getAttribute('data-title-'+lang);
  if(title){document.title=title;}
  for(var i=0;i<buttons.length;i++){
    var on=buttons[i].getAttribute('data-set')===lang;
    buttons[i].className='langbtn'+(on?' on':'');
    buttons[i].setAttribute('aria-pressed',on?'true':'false');
  }
  try{localStorage.setItem('comfy-report-lang',lang);}catch(e){}
}
for(var i=0;i<buttons.length;i++){
  buttons[i].addEventListener('click',function(){set(this.getAttribute('data-set'));});
}
document.addEventListener('keydown',function(e){
  if(e.ctrlKey||e.altKey||e.metaKey){return;}
  var tag=((e.target&&e.target.tagName)||'').toLowerCase();
  if(tag==='input'||tag==='textarea'||tag==='select'){return;}
  if((e.key||'').toLowerCase()==='l'){
    set(root.getAttribute('data-lang')==='en'?'ko':'en');
  }
});
set(root.getAttribute('data-lang')||'ko');
})();
"""


def _lang_buttons(lang):
    return '<div class="langbar" role="group" aria-label="language">' + "".join(
        '<button type="button" class="langbtn{}" data-set="{}" aria-pressed="{}">{}'
        "</button>".format(" on" if code == lang else "", code,
                           "true" if code == lang else "false",
                           _esc(i18n.LANG_LABELS[code]))
        for code in i18n.LANGS) + "</div>"


def has_translation(d):
    """True once any commit carries a Korean subject."""
    for entry in ([d["core"]] if d.get("core") else []) + (d.get("nodes_changed") or []):
        for commit in entry.get("commits") or []:
            if commit.get("subject_ko"):
                return True
    return False


def render_html(d, generated_at=None, lang=i18n.DEFAULT_LANG, engine="google"):
    """The report page. Both languages are in the file; a button picks one.

    `engine` names what translated the commit subjects ("google" or "llm"),
    for the small note under the title.
    """
    lang = i18n.normalize(lang)
    title_ko, title_en = i18n.both("title")
    title = title_ko if lang == "ko" else title_en

    parts = ["<!doctype html>",
             '<html lang="{0}" data-lang="{0}" data-title-ko="{1}" data-title-en="{2}">'
             .format(lang, _esc(title_ko), _esc(title_en)),
             '<head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>" + _esc(title) + "</title>",
             "<style>" + _CSS + "</style>",
             "<script>" + _BOOT_JS + "</script>",
             "</head><body>",
             '<header class="top"><div><h1>' + _s("title") + "</h1>"
             + '<div class="meta">' + _esc(generated_at or "")
             + '<span class="hint">' + _s("lang_hint") + "</span></div></div>"
             + _lang_buttons(lang) + "</header>"]

    if has_translation(d):
        note = "mt_note_llm" if engine == "llm" else "mt_note_google"
        parts.append('<p class="mtnote lg-ko">' + _esc(i18n.t(note, "ko")) + "</p>")

    if not d["has_changes"]:
        parts.append('<p class="none">' + _s("no_changes") + "</p>")
        parts.append("<script>" + _TOGGLE_JS + "</script>")
        parts.append("</body></html>")
        return "\n".join(parts)

    core = d["core"]
    core_count = len(core.get("commits") or []) if core else 0
    risky_count = len([p for p in d["packages_changed"] if p.get("risky")])
    cards = [("card_core_commits", core_count, False),
             ("card_nodes_changed", len(d["nodes_changed"]), False),
             ("card_nodes_added", len(d["nodes_added"]), False),
             ("card_nodes_removed", len(d["nodes_removed"]), False),
             ("card_packages_changed", len(d["packages_changed"]), False),
             ("card_risky", risky_count, risky_count > 0)]
    parts.append('<div class="summary">' + "".join(
        '<div class="card{}"><b>{}</b><span>{}</span></div>'.format(
            " warn" if warn else "", value, _s(key)) for key, value, warn in cards)
        + "</div>")

    if core:
        parts.append("<h2>" + _s("sec_core") + "</h2>")
        parts.append(_entry_block(dict(core, name="ComfyUI", kind="git"), "core"))

    if d["nodes_changed"]:
        parts.append("<h2>" + _s("sec_nodes_changed", n=len(d["nodes_changed"])) + "</h2>")
        parts += [_entry_block(n, n.get("kind") or "cnr") for n in d["nodes_changed"]]

    for key, heading in (("nodes_added", "sec_nodes_added"),
                         ("nodes_removed", "sec_nodes_removed")):
        if d[key]:
            parts.append("<h2>" + _s(heading, n=len(d[key])) + "</h2>")
            parts.append('<div class="chips">' + "".join(
                '<div class="chip">{} <span class="rev">{}</span></div>'.format(
                    _esc(n["name"]), _esc(n.get("new") or n.get("old") or ""))
                for n in d[key]) + "</div>")

    pkg_rows = []
    for p in d["packages_changed"]:
        cls = "pkg risky" if p.get("risky") else "pkg"
        pkg_rows.append('<tr class="{}"><td>{}</td><td>{}</td><td>&rarr;</td>'
                        "<td>{}</td></tr>".format(
                            cls, _esc(p["name"]), _esc(p["old"]), _esc(p["new"])))
    for p in d["packages_added"]:
        pkg_rows.append('<tr class="pkg"><td>{}</td><td class="none">{}</td>'
                        "<td>&rarr;</td><td>{}</td></tr>".format(
                            _esc(p["name"]), _s("pkg_new"), _esc(p["new"])))
    for p in d["packages_removed"]:
        pkg_rows.append('<tr class="pkg"><td>{}</td><td>{}</td><td>&rarr;</td>'
                        '<td class="none">{}</td></tr>'.format(
                            _esc(p["name"]), _esc(p["old"]), _s("pkg_removed")))
    if pkg_rows:
        parts.append("<h2>" + _s("sec_packages", n=len(pkg_rows)) + "</h2>")
        parts.append('<table class="pkgs">' + "".join(pkg_rows) + "</table>")

    parts.append("<script>" + _TOGGLE_JS + "</script>")
    parts.append("</body></html>")
    return "\n".join(parts)


# ------------------------------------------------------------- collecting --

def collect_packages(distributions=None):
    """Installed distributions as {name: version}; first wins on duplicates."""
    if distributions is None:
        distributions = metadata.distributions()
    packages = {}
    for dist in distributions:
        try:
            name = dist.metadata["Name"]
        except Exception:
            name = None
        if not name or name in packages:
            continue
        packages[name] = dist.version
    return packages


def _read_pyproject(node_path):
    path = os.path.join(node_path, "pyproject.toml")
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _pyproject_repository(text):
    if not text or tomllib is None:
        return None
    try:
        urls = tomllib.loads(text).get("project", {}).get("urls", {})
    except (tomllib.TOMLDecodeError, ValueError, AttributeError):
        return None
    for key in ("Repository", "repository", "Source", "Homepage", "homepage"):
        if urls.get(key):
            return urls[key]
    return None


def collect_nodes(custom_nodes_dir, git_reader=None):
    """Read a revision for every entry under custom_nodes."""
    git_reader = git_reader or read_git_head
    if not os.path.isdir(custom_nodes_dir):
        return {}

    names = [n for n in sorted(os.listdir(custom_nodes_dir))
             if n not in _SKIP_DIRS and not n.startswith(".")
             and os.path.isdir(os.path.join(custom_nodes_dir, n))]

    git_names = [n for n in names
                 if os.path.exists(os.path.join(custom_nodes_dir, n, ".git"))]
    cnr_names = [n for n in names if n not in set(git_names)]

    nodes = {}
    if git_names:
        with futures.ThreadPoolExecutor(max_workers=12) as pool:
            read = pool.map(git_reader,
                            [os.path.join(custom_nodes_dir, n) for n in git_names])
            for name, info in zip(git_names, read):
                nodes[name] = {"kind": "git", "sha": (info or {}).get("sha"),
                               "remote": (info or {}).get("remote")}
    for name in cnr_names:
        text = _read_pyproject(os.path.join(custom_nodes_dir, name))
        nodes[name] = {"kind": "cnr", "version": parse_pyproject_version(text),
                       "remote": _pyproject_repository(text)}
    return nodes


def find_comfy_dir(start, max_up=3):
    """Locate the directory that holds custom_nodes, climbing at most max_up levels.

    Handles a script sitting next to ComfyUI (portable / Easy-Install layouts),
    inside the ComfyUI directory itself, or one level deeper.
    """
    current = os.path.abspath(start)
    for _ in range(max_up + 1):
        if os.path.isdir(os.path.join(current, "custom_nodes")):
            return current
        for child in sorted(os.listdir(current)) if os.path.isdir(current) else []:
            if child.lower() not in ("comfyui", "comfy"):
                continue
            candidate = os.path.join(current, child)
            if os.path.isdir(os.path.join(candidate, "custom_nodes")):
                return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


# --------------------------------------------------------------------- git --

def _run_git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", cwd] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "git failed").strip())
    return result.stdout


def read_git_head(repo_path):
    """Current SHA and origin remote of a repository."""
    try:
        sha = _run_git(repo_path, "rev-parse", "HEAD").strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return {"sha": None, "remote": None}
    try:
        remote = _run_git(repo_path, "remote", "get-url", "origin").strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        remote = None
    return {"sha": sha, "remote": remote}


def read_git_log(repo_path, old, new):
    """Commits in old..new. Raises so the caller can fall back to an empty list."""
    return parse_git_log(_run_git(repo_path, "log", "--no-merges",
                                  "--pretty=format:%h%x09%as%x09%s",
                                  "{}..{}".format(old, new)))


def enrich_commits(d, comfy_dir, log_reader=None, limit=COMMIT_LIMIT):
    """Attach a commit list to every git-backed entry of the diff."""
    log_reader = log_reader or read_git_log
    targets = []
    if d.get("core"):
        targets.append((comfy_dir, d["core"]))
    for entry in d.get("nodes_changed", []):
        if entry.get("kind") == "git":
            targets.append((os.path.join(comfy_dir, "custom_nodes", entry["name"]), entry))
    if not targets:
        return d

    def fetch(target):
        path, entry = target
        try:
            return log_reader(path, entry["old"], entry["new"]) or []
        except Exception:
            return []

    with futures.ThreadPoolExecutor(max_workers=12) as pool:
        for (_, entry), commits in zip(targets, pool.map(fetch, targets)):
            entry["commits"] = commits[:limit]
            entry["commits_omitted"] = max(0, len(commits) - limit)
    return d


# ------------------------------------------------------------------- main --

def collect_state(comfy_dir):
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tool_version": __version__,
        "core": read_git_head(comfy_dir),
        "nodes": collect_nodes(os.path.join(comfy_dir, "custom_nodes")),
        "packages": collect_packages(),
    }


def _load_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_state(path, state):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


USAGE_EN = """ComfyUI update report {version}

  update_report.py [options]

  --comfy PATH     path to the ComfyUI directory (auto-detected otherwise)
  --lang ko|en     console language, and the language the report opens in
                   (default: ko)
  --baseline       overwrite the snapshot without reporting
  --no-open        do not open the HTML report in a browser
  --no-color       plain console output
  --translator X   what translates commit subjects into Korean:
                   google (default, no setup) or llm (a local LLM server)
  --no-translate   do not translate commit subjects at all
  --llm URL        OpenAI-compatible endpoint; implies --translator llm
                   (default: LM Studio, llama.cpp and Ollama, in that order)
  --llm-model ID   model to translate with (default: whatever is loaded)
  --llm-key KEY    API key, if the server wants one (LM Studio does when its
                   local API token is on). Also read from COMFY_REPORT_LLM_KEY,
                   LM_API_TOKEN or OPENAI_API_KEY.
  --help           this message

  Settings can also live in _update-report/config.json:
  {{"lang": "ko", "translate": true, "translator": "google",
    "llm_url": null, "llm_model": null, "llm_key": null}}
"""

USAGE_KO = """ComfyUI 업데이트 리포트 {version}

  update_report.py [옵션]

  --comfy 경로     ComfyUI 폴더 경로 (기본: 자동 탐지)
  --lang ko|en     콘솔 언어이자 리포트가 처음 열릴 때의 언어 (기본: ko)
  --baseline       리포트 없이 기준점만 새로 저장
  --no-open        HTML 리포트를 브라우저로 열지 않음
  --no-color       색 없는 콘솔 출력
  --translator X   커밋 제목을 한글로 번역할 방법:
                   google (기본, 설정 불필요) 또는 llm (로컬 LLM 서버)
  --no-translate   커밋 제목을 아예 번역하지 않음
  --llm URL        OpenAI 호환 엔드포인트. 지정하면 --translator llm 이 됩니다
                   (기본: LM Studio → llama.cpp → Ollama 순으로 탐색)
  --llm-model ID   번역에 쓸 모델 (기본: 로드되어 있는 모델)
  --llm-key KEY    서버가 API 키를 요구할 때 (LM Studio 는 로컬 API 토큰을
                   켜두면 요구합니다). COMFY_REPORT_LLM_KEY, LM_API_TOKEN,
                   OPENAI_API_KEY 환경변수로도 읽습니다.
  --help           이 도움말

  _update-report/config.json 에 설정을 넣어둘 수도 있습니다:
  {{"lang": "ko", "translate": true, "translator": "google",
    "llm_url": null, "llm_model": null, "llm_key": null}}
"""


def load_config(path):
    """Optional settings file next to the script; absent or broken -> {}."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _opt(argv, name, default=None):
    """The value after `--name`, or default."""
    if name in argv:
        index = argv.index(name) + 1
        if index < len(argv):
            return argv[index]
    return default


TRANSLATORS = ("google", "llm")
_OFF_WORDS = ("0", "off", "false", "no", "none")


def resolve_options(argv, config, env=None):
    """Command line beats environment beats config.json beats the defaults."""
    env = os.environ if env is None else env

    lang = _opt(argv, "--lang") or env.get("COMFY_REPORT_LANG") or config.get("lang")
    translate_on = config.get("translate", True)
    if env.get("COMFY_REPORT_TRANSLATE", "").strip().lower() in _OFF_WORDS:
        translate_on = False
    if "--no-translate" in argv:
        translate_on = False

    url = _opt(argv, "--llm") or env.get("COMFY_REPORT_LLM") or config.get("llm_url")
    model = (_opt(argv, "--llm-model") or env.get("COMFY_REPORT_LLM_MODEL")
             or config.get("llm_model"))
    key = (_opt(argv, "--llm-key") or translate.key_from_env(env)
           or config.get("llm_key"))

    # Google Translate unless a local LLM was asked for -- by name, or by
    # pointing at one.
    chosen = (_opt(argv, "--translator") or env.get("COMFY_REPORT_TRANSLATOR")
              or config.get("translator") or "")
    chosen = str(chosen).strip().lower()
    if chosen in _OFF_WORDS:
        translate_on = False
        chosen = ""
    if chosen not in TRANSLATORS:
        chosen = "llm" if (url or model) else "google"
    return {
        "lang": i18n.normalize(lang),
        "translate": bool(translate_on),
        "translator": chosen,
        "llm_url": url or None,
        "llm_model": model or None,
        "llm_key": key or None,
    }


def make_translator(options, cache_path):
    """The translator the options ask for."""
    if options.get("translator") == "llm":
        endpoints = [options["llm_url"]] if options.get("llm_url") else None
        return translate.LLMTranslator(cache_path=cache_path, endpoints=endpoints,
                                       model=options.get("llm_model"),
                                       key=options.get("llm_key"))
    return translate.GoogleTranslator(cache_path=cache_path)


def translate_commits(d, options, cache_path):
    """Korean subjects onto the diff.

    Returns what the console needs to say about it: {count, engine, model,
    needs_key, unreachable}. Never raises: a report without Korean commit
    subjects is still a report.
    """
    result = {"count": 0, "engine": None, "model": None,
              "needs_key": False, "unreachable": False}
    if not options["translate"]:
        return result
    translator = make_translator(options, cache_path)
    result["engine"] = translator.name
    try:
        result["count"] = translate.apply_to_diff(d, translator)
    except Exception:
        return result
    result["model"] = translator.used_model
    result["needs_key"] = translator.needs_key
    result["unreachable"] = translator.unreachable
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    here = os.path.dirname(os.path.abspath(__file__))
    options = resolve_options(argv, load_config(os.path.join(here, "config.json")))
    lang = options["lang"]

    if "--help" in argv or "-h" in argv:
        usage = USAGE_KO if lang == "ko" else USAGE_EN
        print(usage.format(version=__version__))
        return 0

    if "--comfy" in argv:
        comfy_dir = os.path.abspath(argv[argv.index("--comfy") + 1])
    else:
        comfy_dir = find_comfy_dir(here)

    color = "--no-color" not in argv
    if not comfy_dir or not os.path.isdir(os.path.join(comfy_dir, "custom_nodes")):
        print(_c("r", i18n.t("c_no_comfy", lang, path=here), color))
        print(i18n.t("c_no_comfy_hint", lang))
        return 2

    state_path = os.path.join(here, "state.json")
    reports_dir = os.path.join(here, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    new_state = collect_state(comfy_dir)
    old_state = _load_state(state_path)

    if old_state is None or "--baseline" in argv:
        _save_state(state_path, new_state)
        print("\n" + _c("g", ":::::::::::  " + i18n.t("title", lang)
                        + "  :::::::::::", color))
        print("  " + i18n.t("c_baseline", lang))
        print("  ComfyUI: {}".format(comfy_dir))
        print("  " + i18n.t("c_recorded", lang, nodes=len(new_state["nodes"]),
                            packages=len(new_state["packages"])) + "\n")
        return 0

    d = diff_state(old_state, new_state)
    enrich_commits(d, comfy_dir)
    tr = translate_commits(d, options, os.path.join(here, "translations.json"))

    now = datetime.now()
    report_path = os.path.join(reports_dir, now.strftime("%Y-%m-%d_%H%M") + ".html")
    with io.open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_html(d, generated_at=now.strftime("%Y-%m-%d %H:%M"), lang=lang,
                             engine=tr["engine"] or options["translator"]))

    print(render_console(d, report_path=report_path if d["has_changes"] else None,
                         generated_at=now.strftime("%Y-%m-%d %H:%M"), color=color,
                         lang=lang, translated=tr["count"], model=tr["model"],
                         needs_key=tr["needs_key"], engine=tr["engine"],
                         unreachable=tr["unreachable"]))
    _save_state(state_path, new_state)

    if d["has_changes"] and "--no-open" not in argv:
        try:
            webbrowser.open("file:///" + os.path.abspath(report_path).replace(os.sep, "/"))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
