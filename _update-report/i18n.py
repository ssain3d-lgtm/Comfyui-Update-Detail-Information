# -*- coding: utf-8 -*-
"""Korean/English wording for the report.

Every user-facing string lives here as a (ko, en) pair. The HTML report ships
both languages and toggles between them in the browser, so this table is the
single place a new string has to be written twice.
"""
import unicodedata

LANGS = ("ko", "en")
DEFAULT_LANG = "ko"

STRINGS = {
    # -- shared ------------------------------------------------------------
    "title": ("ComfyUI 업데이트 리포트", "ComfyUI update report"),
    "no_changes": ("지난 리포트 이후 바뀐 것이 없습니다.",
                   "No changes since the last report."),

    # -- console -----------------------------------------------------------
    "c_core": ("코어", "core"),
    "c_nodes": ("노드", "nodes"),
    "c_packages": ("패키지", "packages"),
    "c_report": ("리포트", "report"),
    "c_commits": ("커밋 {n}개", "{n} commits"),
    "c_commits_none": ("커밋 목록 없음", "commit list unavailable"),
    "c_node_counts": ("변경 {changed} / 추가 {added} / 제거 {removed}",
                      "changed {changed} / added {added} / removed {removed}"),
    "c_pkg_counts": ("변경 {n}", "changed {n}"),
    "c_baseline": ("기준점을 저장했습니다. 다음 실행부터 변경내역이 나옵니다.",
                   "Baseline saved. The next run will show what changed."),
    "c_recorded": ("노드 {nodes}개 / 패키지 {packages}개 기록",
                   "Recorded {nodes} nodes / {packages} packages"),
    "c_no_comfy": ("[오류] {path} 근처에서 ComfyUI 폴더를 찾지 못했습니다.",
                   "[ERROR] Could not find a ComfyUI directory near {path}"),
    "c_no_comfy_hint": ("       직접 지정하세요:  update_report.py --comfy <ComfyUI 폴더>",
                        "        Pass it explicitly:  update_report.py --comfy <path to ComfyUI>"),
    "c_translate_label": ("번역", "translated"),
    "c_translated": ("커밋 제목 {n}개 ({model})", "{n} commit subjects ({model})"),
    "c_llm_auth": ("LLM 서버가 API 키를 요구합니다 (--llm-key)",
                   "the LLM server wants an API key (--llm-key)"),

    # -- html summary cards -------------------------------------------------
    "card_core_commits": ("코어 커밋", "core commits"),
    "card_nodes_changed": ("변경된 노드", "nodes changed"),
    "card_nodes_added": ("추가된 노드", "nodes added"),
    "card_nodes_removed": ("제거된 노드", "nodes removed"),
    "card_packages_changed": ("변경된 패키지", "packages changed"),
    "card_risky": ("위험 패키지", "risky packages"),

    # -- html sections ------------------------------------------------------
    "sec_core": ("ComfyUI 코어", "ComfyUI core"),
    "sec_nodes_changed": ("업데이트된 노드 ({n})", "Updated nodes ({n})"),
    "sec_nodes_added": ("새로 설치된 노드 ({n})", "Newly installed nodes ({n})"),
    "sec_nodes_removed": ("제거된 노드 ({n})", "Removed nodes ({n})"),
    "sec_packages": ("Python 패키지 ({n})", "Python packages ({n})"),

    # -- html details -------------------------------------------------------
    "commits_none": ("커밋 목록을 가져올 수 없습니다 "
                     "(강제 푸시, 얕은 클론, 또는 이전 커밋이 사라진 경우).",
                     "Commit list unavailable "
                     "(force push, shallow clone, or the old commit is gone)."),
    "more_commits": ("... 외 {n}개", "... and {n} more"),
    "pkg_new": ("(신규)", "(new)"),
    "pkg_removed": ("(제거됨)", "(removed)"),
    "lang_hint": ("L 키로 전환", "press L to switch"),
    "mt_note": ("커밋 제목의 한글은 로컬 LLM 자동 번역입니다.",
                "Korean commit subjects are machine-translated by a local LLM."),
}

# Not translated: the language buttons name their own language.
LANG_LABELS = {"ko": "한국어", "en": "English"}


def normalize(lang):
    """Anything unknown falls back to the default language."""
    lang = (lang or "").strip().lower()
    if lang in ("kr", "korean", "한국어", "한글"):
        lang = "ko"
    elif lang in ("eng", "english", "영어"):
        lang = "en"
    return lang if lang in LANGS else DEFAULT_LANG


def t(key, lang=DEFAULT_LANG, **fmt):
    """One string in one language, with {placeholders} filled in."""
    pair = STRINGS[key]
    text = pair[0] if normalize(lang) == "ko" else pair[1]
    return text.format(**fmt) if fmt else text


def both(key, **fmt):
    """(korean, english) for a key -- what the HTML needs to ship both."""
    return t(key, "ko", **fmt), t(key, "en", **fmt)


def width(text):
    """Console cell width: CJK glyphs take two columns."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def pad(text, columns):
    """Left-align to a column count, counting CJK glyphs as two."""
    return text + " " * max(0, columns - width(text))
