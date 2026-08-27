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

__version__ = "1.0.0"

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


def _c(code, text, color):
    return _ANSI[code] + text + _ANSI["0"] if color else text


def _short(sha):
    return (sha or "?")[:8]


def render_console(d, report_path=None, generated_at=None, color=False):
    """A few ASCII lines for the update window."""
    title = "ComfyUI update report"
    if generated_at:
        title += " (" + generated_at + ")"
    lines = ["", _c("g", "::::::::::: " + title + " :::::::::::", color)]

    if not d["has_changes"]:
        lines += ["  " + _c("d", "No changes since the last report.", color), ""]
        return "\n".join(lines)

    core = d["core"]
    if core:
        n = len(core.get("commits") or [])
        count = "{} commits".format(n) if n else "commit list unavailable"
        lines.append("  core        {} -> {}  ({})".format(
            _short(core["old"]), _c("y", _short(core["new"]), color), count))

    if d["nodes_changed"] or d["nodes_added"] or d["nodes_removed"]:
        lines.append("  nodes       changed {} / added {} / removed {}".format(
            len(d["nodes_changed"]), len(d["nodes_added"]), len(d["nodes_removed"])))

    pkg_total = len(d["packages_changed"]) + len(d["packages_added"]) + len(d["packages_removed"])
    if pkg_total:
        line = "  packages    changed {}".format(pkg_total)
        risky = [p for p in d["packages_changed"] if p.get("risky")]
        if risky:
            detail = ", ".join("{} {} -> {}".format(p["name"], p["old"], p["new"])
                               for p in risky)
            line += "  " + _c("r", "[!] " + detail, color)
        lines.append(line)

    if report_path:
        lines.append("  report      " + str(report_path))
    lines.append("")
    return "\n".join(lines)


def _esc(text):
    return html.escape(str(text) if text is not None else "")


def _commit_rows(commits, extra_count=0):
    if not commits:
        return ('<p class="none">Commit list unavailable '
                "(force push, shallow clone, or the old commit is gone).</p>")
    rows = "".join(
        '<tr><td class="date">{}</td><td class="sha">{}</td><td>{}</td></tr>'.format(
            _esc(c["date"]), _esc(c["sha"]), _esc(c["subject"])) for c in commits)
    more = ('<tr><td></td><td></td><td class="none">... and {} more</td></tr>'.format(extra_count)
            if extra_count else "")
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
.meta{color:#7c8794;font-size:12px;margin-bottom:24px}
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


def render_html(d, generated_at=None, title="ComfyUI update report"):
    parts = ["<!doctype html>",
             '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>" + _esc(title) + "</title><style>" + _CSS + "</style></head><body>",
             "<h1>" + _esc(title) + "</h1>",
             '<div class="meta">' + _esc(generated_at or "") + "</div>"]

    if not d["has_changes"]:
        parts.append('<p class="none">No changes since the last report.</p>')
        parts.append("</body></html>")
        return "\n".join(parts)

    core = d["core"]
    core_count = len(core.get("commits") or []) if core else 0
    risky_count = len([p for p in d["packages_changed"] if p.get("risky")])
    cards = [("core commits", core_count, False),
             ("nodes changed", len(d["nodes_changed"]), False),
             ("nodes added", len(d["nodes_added"]), False),
             ("nodes removed", len(d["nodes_removed"]), False),
             ("packages changed", len(d["packages_changed"]), False),
             ("risky packages", risky_count, risky_count > 0)]
    parts.append('<div class="summary">' + "".join(
        '<div class="card{}"><b>{}</b><span>{}</span></div>'.format(
            " warn" if warn else "", value, _esc(label)) for label, value, warn in cards)
        + "</div>")

    if core:
        parts.append("<h2>ComfyUI core</h2>")
        parts.append(_entry_block(dict(core, name="ComfyUI", kind="git"), "core"))

    if d["nodes_changed"]:
        parts.append("<h2>Updated nodes ({})</h2>".format(len(d["nodes_changed"])))
        parts += [_entry_block(n, n.get("kind") or "cnr") for n in d["nodes_changed"]]

    for key, heading in (("nodes_added", "Newly installed nodes"),
                         ("nodes_removed", "Removed nodes")):
        if d[key]:
            parts.append("<h2>{} ({})</h2>".format(heading, len(d[key])))
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
        pkg_rows.append('<tr class="pkg"><td>{}</td><td class="none">(new)</td>'
                        "<td>&rarr;</td><td>{}</td></tr>".format(
                            _esc(p["name"]), _esc(p["new"])))
    for p in d["packages_removed"]:
        pkg_rows.append('<tr class="pkg"><td>{}</td><td>{}</td><td>&rarr;</td>'
                        '<td class="none">(removed)</td></tr>'.format(
                            _esc(p["name"]), _esc(p["old"])))
    if pkg_rows:
        parts.append("<h2>Python packages ({})</h2>".format(len(pkg_rows)))
        parts.append('<table class="pkgs">' + "".join(pkg_rows) + "</table>")

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


USAGE = """ComfyUI update report {version}

  update_report.py [options]

  --comfy PATH   path to the ComfyUI directory (auto-detected otherwise)
  --baseline     overwrite the snapshot without reporting
  --no-open      do not open the HTML report in a browser
  --no-color     plain console output
  --help         this message
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--help" in argv or "-h" in argv:
        print(USAGE.format(version=__version__))
        return 0

    here = os.path.dirname(os.path.abspath(__file__))
    if "--comfy" in argv:
        comfy_dir = os.path.abspath(argv[argv.index("--comfy") + 1])
    else:
        comfy_dir = find_comfy_dir(here)

    color = "--no-color" not in argv
    if not comfy_dir or not os.path.isdir(os.path.join(comfy_dir, "custom_nodes")):
        print(_c("r", "[ERROR] Could not find a ComfyUI directory near " + here, color))
        print("        Pass it explicitly:  update_report.py --comfy <path to ComfyUI>")
        return 2

    state_path = os.path.join(here, "state.json")
    reports_dir = os.path.join(here, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    new_state = collect_state(comfy_dir)
    old_state = _load_state(state_path)

    if old_state is None or "--baseline" in argv:
        _save_state(state_path, new_state)
        print("\n" + _c("g", ":::::::::::  ComfyUI update report  :::::::::::", color))
        print("  Baseline saved. The next run will show what changed.")
        print("  ComfyUI: {}".format(comfy_dir))
        print("  Recorded {} nodes / {} packages\n".format(
            len(new_state["nodes"]), len(new_state["packages"])))
        return 0

    d = diff_state(old_state, new_state)
    enrich_commits(d, comfy_dir)

    now = datetime.now()
    report_path = os.path.join(reports_dir, now.strftime("%Y-%m-%d_%H%M") + ".html")
    with io.open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_html(d, generated_at=now.strftime("%Y-%m-%d %H:%M")))

    print(render_console(d, report_path=report_path if d["has_changes"] else None,
                         generated_at=now.strftime("%Y-%m-%d %H:%M"), color=color))
    _save_state(state_path, new_state)

    if d["has_changes"] and "--no-open" not in argv:
        try:
            webbrowser.open("file:///" + os.path.abspath(report_path).replace(os.sep, "/"))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
