# -*- coding: utf-8 -*-
"""Add one line to the update .bat files so the report runs after every update.

Windows only -- the hook is a .bat line. On Linux/macOS run update_report.py
yourself after updating.

Update launchers get overwritten by their own updaters (ComfyUI-Easy-Install
rewrites its Update*.bat on every self-update), which drops the injected line.
Re-run this script (or reinject.bat) to put it back; it is idempotent.
"""
import io
import os
import re
import sys

CRLF = "\r\n"
HOOK_MARKER = "Update-Report.bat"
_ANCHOR_RE = re.compile(r'^\s*call\s+"Start ComfyUI\.bat"\s*$', re.IGNORECASE)
_UPDATE_RE = re.compile(r"^update.*\.bat$", re.IGNORECASE)
_LABEL_RE = re.compile(r"^\s*:[^:\s]")
_PAUSE_RE = re.compile(r"^\s*pause\s*$", re.IGNORECASE)
_SEARCH_DIRS = ["", "update"]


def hook_lines(rel=""):
    """The two lines to inject; rel is the path from the bat back to the launcher."""
    target = "%~dp0" + (rel + "\\" if rel else "") + HOOK_MARKER
    return [":: ---- ComfyUI update report ----",
            'if exist "{0}" call "{0}" --no-pause'.format(target)]


def inject_hook_text(text, rel="", fallback_append=False):
    """Put the hook just above the last `call "Start ComfyUI.bat"`.

    Already hooked -> the text unchanged. No anchor -> appended at the end when
    fallback_append is set, otherwise None. Whatever line ending the file
    already uses is what gets written back.
    """
    if HOOK_MARKER in text:
        return text

    eol = CRLF if CRLF in text else "\n"
    lines = text.split("\n")
    trailing_newline = lines and lines[-1] == ""
    if trailing_newline:
        lines.pop()
    lines = [line.rstrip("\r") for line in lines]

    def joined(parts):
        return eol.join(parts) + (eol if trailing_newline else "")

    anchors = [i for i, line in enumerate(lines) if _ANCHOR_RE.match(line)]
    if anchors:
        at = anchors[-1]
        return joined(lines[:at] + hook_lines(rel) + [""] + lines[at:])
    if not fallback_append or any(_LABEL_RE.match(line) for line in lines):
        # Past a `:label` the tail belongs to a subroutine, so appending there
        # would run the hook on every call to it -- leave that file alone.
        return None

    while lines and not lines[-1].strip():
        lines.pop()
    at = len(lines)
    if lines and _PAUSE_RE.match(lines[-1]):
        at -= 1
    return joined(lines[:at] + [""] + hook_lines(rel) + [""] + lines[at:])


def find_update_bats(root):
    """Update launchers of any ComfyUI distribution, with their path back to root.

    Only one layer is returned. A root-level updater (Easy-Install) normally
    calls update\\update_comfyui.bat itself, so hooking both layers would run
    the report twice -- and the second run would already see an empty diff.
    Portable installs have no root-level updater, so they fall through to
    the update directory.
    """
    for sub in _SEARCH_DIRS:
        directory = os.path.join(root, sub) if sub else root
        if not os.path.isdir(directory):
            continue
        found = []
        for name in sorted(os.listdir(directory)):
            if not _UPDATE_RE.match(name) or name.lower() == HOOK_MARKER.lower():
                continue
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                found.append({"path": path, "rel": ".." if sub else ""})
        if found:
            return found
    return []


def _process(entry):
    path, rel = entry["path"], entry["rel"]
    # latin-1 round-trips bytes unchanged, and the hook itself is pure ASCII,
    # so a bat written in any codepage survives untouched.
    with io.open(path, "r", encoding="latin-1", newline="") as fh:
        original = fh.read()

    if HOOK_MARKER in original:
        return "already hooked"

    patched = inject_hook_text(original, rel=rel, fallback_append=True)
    if patched is None:
        return "skipped"

    backup = path + ".bak-hook"
    if not os.path.exists(backup):
        with io.open(backup, "w", encoding="latin-1", newline="") as fh:
            fh.write(original)
    with io.open(path, "w", encoding="latin-1", newline="") as fh:
        fh.write(patched)
    return "hooked"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root = os.path.abspath(argv[0]) if argv and os.path.isdir(argv[0]) else default_root

    if os.name != "nt":
        print("The .bat hook is Windows only. Run update_report.py after updating instead.")
        return 0

    bats = find_update_bats(root)
    if not bats:
        print("No update .bat found in {}".format(root))
        print("Run Update-Report.bat by hand after each update.")
        return 1

    for entry in bats:
        print("  {:<44} {}".format(os.path.relpath(entry["path"], root), _process(entry)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
