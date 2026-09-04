# -*- coding: utf-8 -*-
"""Tests for update_report.py -- run with: python test_update_report.py"""
import io
import os
import shutil
import sys
import tempfile
import unittest

# An embedded python (._pth) does not put the script directory on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n
import translate as translate_module
import update_report
from update_report import (collect_nodes, collect_packages, compare_url, diff_state,
                           enrich_commits, find_comfy_dir, parse_git_log,
                           parse_pyproject_version, render_console, render_html)

NL = chr(10)
PYPROJECT_VERSIONED = "[project]" + NL + 'version = "1.27.4"' + NL
PYPROJECT_WITH_URL = (PYPROJECT_VERSIONED + "[project.urls]" + NL
                      + 'Repository = "https://github.com/a/b"' + NL)


def state(core_sha="aaa111", nodes=None, packages=None):
    return {
        "core": {"sha": core_sha, "remote": "https://github.com/Comfy-Org/ComfyUI"},
        "nodes": nodes or {},
        "packages": packages or {},
    }


def git_node(sha, remote="https://github.com/x/y"):
    return {"kind": "git", "sha": sha, "remote": remote}


def cnr_node(version, remote="https://github.com/x/y"):
    return {"kind": "cnr", "version": version, "remote": remote}


def d_names(d, key):
    return [n["name"] for n in d[key]]


class DiffCore(unittest.TestCase):
    def test_reports_core_sha_change(self):
        d = diff_state(state(core_sha="aaa111"), state(core_sha="bbb222"))
        self.assertEqual(d["core"], {"old": "aaa111", "new": "bbb222",
                                     "remote": "https://github.com/Comfy-Org/ComfyUI"})

    def test_core_is_none_when_sha_unchanged(self):
        d = diff_state(state(core_sha="aaa111"), state(core_sha="aaa111"))
        self.assertIsNone(d["core"])


class DiffNodes(unittest.TestCase):
    def test_reports_git_node_whose_sha_moved(self):
        old = state(nodes={"was-node-suite": git_node("1f2a3b4")})
        new = state(nodes={"was-node-suite": git_node("9c8d7e6")})
        changed = diff_state(old, new)["nodes_changed"][0]
        self.assertEqual((changed["name"], changed["old"], changed["new"], changed["kind"]),
                         ("was-node-suite", "1f2a3b4", "9c8d7e6", "git"))

    def test_reports_cnr_node_whose_version_moved(self):
        old = state(nodes={"ComfyUI-Crystools": cnr_node("1.27.4")})
        new = state(nodes={"ComfyUI-Crystools": cnr_node("1.28.0")})
        changed = diff_state(old, new)["nodes_changed"][0]
        self.assertEqual((changed["old"], changed["new"], changed["kind"]),
                         ("1.27.4", "1.28.0", "cnr"))

    def test_unchanged_node_is_not_reported(self):
        s = state(nodes={"cg-use-everywhere": git_node("abc")})
        self.assertEqual(diff_state(s, s)["nodes_changed"], [])

    def test_node_present_only_in_new_state_is_added(self):
        d = diff_state(state(nodes={}), state(nodes={"ComfyUI-NewThing": cnr_node("0.1.0")}))
        self.assertEqual(d_names(d, "nodes_added"), ["ComfyUI-NewThing"])
        self.assertEqual(d["nodes_added"][0]["new"], "0.1.0")
        self.assertEqual(d["nodes_changed"], [])

    def test_node_present_only_in_old_state_is_removed(self):
        d = diff_state(state(nodes={"ComfyUI-OldThing": git_node("abc")}), state(nodes={}))
        self.assertEqual(d_names(d, "nodes_removed"), ["ComfyUI-OldThing"])
        self.assertEqual(d["nodes_changed"], [])

    def test_node_switching_from_git_to_cnr_counts_as_changed(self):
        old = state(nodes={"ComfyUI-Easy-Use": git_node("abc1234")})
        new = state(nodes={"ComfyUI-Easy-Use": cnr_node("1.3.2")})
        changed = diff_state(old, new)["nodes_changed"][0]
        self.assertEqual((changed["old"], changed["new"]), ("abc1234", "1.3.2"))

    def test_changed_nodes_are_sorted_case_insensitively(self):
        old = state(nodes={"zebra": git_node("a"), "Alpha": git_node("a")})
        new = state(nodes={"zebra": git_node("b"), "Alpha": git_node("b")})
        self.assertEqual(d_names(diff_state(old, new), "nodes_changed"), ["Alpha", "zebra"])


class DiffPackages(unittest.TestCase):
    def test_reports_version_change(self):
        d = diff_state(state(packages={"av": "17.0.1"}), state(packages={"av": "16.0.1"}))
        pkg = d["packages_changed"][0]
        self.assertEqual((pkg["name"], pkg["old"], pkg["new"]), ("av", "17.0.1", "16.0.1"))

    def test_flags_av_as_risky(self):
        d = diff_state(state(packages={"av": "17.0.1"}), state(packages={"av": "16.0.1"}))
        self.assertTrue(d["packages_changed"][0]["risky"])

    def test_does_not_flag_ordinary_package_as_risky(self):
        d = diff_state(state(packages={"rich": "13.0.0"}), state(packages={"rich": "14.0.0"}))
        self.assertFalse(d["packages_changed"][0]["risky"])

    def test_flags_risky_package_regardless_of_name_case(self):
        d = diff_state(state(packages={"NumPy": "2.2.6"}), state(packages={"NumPy": "2.4.0"}))
        self.assertTrue(d["packages_changed"][0]["risky"])

    def test_reports_added_and_removed_packages(self):
        d = diff_state(state(packages={"gone": "1.0"}), state(packages={"fresh": "2.0"}))
        self.assertEqual([p["name"] for p in d["packages_added"]], ["fresh"])
        self.assertEqual([p["name"] for p in d["packages_removed"]], ["gone"])
        self.assertEqual(d["packages_changed"], [])


class DiffEmptiness(unittest.TestCase):
    def test_identical_states_produce_an_empty_diff(self):
        s = state(nodes={"a": git_node("x")}, packages={"av": "17.0.1"})
        self.assertFalse(diff_state(s, s)["has_changes"])

    def test_any_single_change_makes_the_diff_non_empty(self):
        d = diff_state(state(packages={"av": "17.0.1"}), state(packages={"av": "16.0.1"}))
        self.assertTrue(d["has_changes"])


class ParsePyprojectVersion(unittest.TestCase):
    def test_reads_version_from_project_table(self):
        self.assertEqual(parse_pyproject_version(
            "[project]" + NL + 'name = "x"' + NL + 'version = "1.27.4"' + NL), "1.27.4")

    def test_returns_none_when_project_has_no_version(self):
        self.assertIsNone(parse_pyproject_version("[project]" + NL + 'name = "x"' + NL))

    def test_ignores_version_belonging_to_another_table(self):
        self.assertIsNone(parse_pyproject_version(
            "[project]" + NL + 'name = "x"' + NL + "[tool.poetry]" + NL + 'version = "9.9"' + NL))

    def test_falls_back_to_regex_when_toml_is_malformed(self):
        self.assertEqual(parse_pyproject_version(
            "[project" + NL + 'version = "0.4.2"' + NL + "not toml at all" + NL), "0.4.2")

    def test_falls_back_to_regex_when_tomllib_is_unavailable(self):
        original = update_report.tomllib
        update_report.tomllib = None
        self.addCleanup(setattr, update_report, "tomllib", original)
        self.assertEqual(parse_pyproject_version(PYPROJECT_VERSIONED), "1.27.4")

    def test_returns_none_for_empty_text(self):
        self.assertIsNone(parse_pyproject_version(""))


class ParseGitLog(unittest.TestCase):
    def test_parses_tab_separated_commit_lines(self):
        commits = parse_git_log("a91f2c4\t2026-08-27\tFix VAE tiling OOM" + NL
                                + "7b3e011\t2026-08-26\tAdd Qwen sampler" + NL)
        self.assertEqual(commits, [
            {"sha": "a91f2c4", "date": "2026-08-27", "subject": "Fix VAE tiling OOM"},
            {"sha": "7b3e011", "date": "2026-08-26", "subject": "Add Qwen sampler"},
        ])

    def test_skips_blank_lines(self):
        self.assertEqual(len(parse_git_log(
            "a\t2026-08-27\tone" + NL + NL + NL + "b\t2026-08-26\ttwo" + NL)), 2)

    def test_keeps_tabs_that_belong_to_the_subject(self):
        commits = parse_git_log("a91f2c4\t2026-08-27\tfix:\tindent bug" + NL)
        self.assertEqual(commits[0]["subject"], "fix:\tindent bug")

    def test_returns_empty_list_for_empty_output(self):
        self.assertEqual(parse_git_log(""), [])


class CompareUrl(unittest.TestCase):
    def test_builds_github_compare_link(self):
        self.assertEqual(
            compare_url("https://github.com/Comfy-Org/ComfyUI", "aaa111", "bbb222"),
            "https://github.com/Comfy-Org/ComfyUI/compare/aaa111...bbb222")

    def test_strips_dot_git_suffix(self):
        self.assertEqual(compare_url("https://github.com/x/y.git", "a", "b"),
                         "https://github.com/x/y/compare/a...b")

    def test_normalizes_ssh_remote(self):
        self.assertEqual(compare_url("git@github.com:x/y.git", "a", "b"),
                         "https://github.com/x/y/compare/a...b")

    def test_returns_none_for_non_github_remote(self):
        self.assertIsNone(compare_url("https://gitlab.com/x/y", "a", "b"))

    def test_returns_none_when_remote_is_missing(self):
        self.assertIsNone(compare_url(None, "a", "b"))


def diff(**over):
    base = {"core": None, "nodes_changed": [], "nodes_added": [], "nodes_removed": [],
            "packages_changed": [], "packages_added": [], "packages_removed": [],
            "has_changes": False}
    base.update(over)
    base["has_changes"] = any(base[k] for k in base if k != "has_changes")
    return base


class RenderConsole(unittest.TestCase):
    def test_shows_core_transition_with_commit_count(self):
        out = render_console(diff(core={
            "old": "d8e7bbc", "new": "a91f2c4", "remote": None,
            "commits": [{"sha": "x", "date": "d", "subject": "s"}] * 12}), lang="en")
        self.assertIn("d8e7bbc", out)
        self.assertIn("a91f2c4", out)
        self.assertIn("12 commits", out)

    def test_shows_node_counts(self):
        out = render_console(diff(
            nodes_changed=[{"name": "a", "kind": "git", "old": "1", "new": "2", "remote": None}],
            nodes_added=[{"name": "b", "kind": "cnr", "new": "0.1", "remote": None}],
            nodes_removed=[{"name": "c", "kind": "git", "old": "9"}]), lang="en")
        self.assertIn("changed 1 / added 1 / removed 1", out)

    def test_names_risky_package_change_in_summary(self):
        out = render_console(diff(packages_changed=[
            {"name": "av", "old": "17.0.1", "new": "16.0.1", "risky": True}]))
        self.assertIn("av", out)
        self.assertIn("17.0.1", out)
        self.assertIn("16.0.1", out)

    def test_does_not_name_ordinary_package_change_in_summary(self):
        out = render_console(diff(packages_changed=[
            {"name": "rich", "old": "13.0", "new": "14.0", "risky": False}]))
        self.assertNotIn("rich", out)

    def test_reports_no_changes(self):
        self.assertIn("No changes", render_console(diff(), lang="en"))

    def test_includes_report_path_when_given(self):
        out = render_console(diff(core={"old": "a", "new": "b", "remote": None, "commits": []}),
                             report_path="reports/2026-08-27_1402.html")
        self.assertIn("reports/2026-08-27_1402.html", out)

    def test_stays_ascii_so_legacy_consoles_do_not_garble_it(self):
        out = render_console(diff(
            core={"old": "a", "new": "b", "remote": None, "commits": []},
            nodes_changed=[{"name": "n", "kind": "git", "old": "1", "new": "2", "remote": None}],
            packages_changed=[{"name": "av", "old": "1", "new": "2", "risky": True}]),
            report_path="r.html", lang="en")
        out.encode("ascii")


class RenderHtml(unittest.TestCase):
    def test_escapes_html_in_commit_subject(self):
        page = render_html(diff(core={"old": "a", "new": "b", "remote": None, "commits": [
            {"sha": "c1", "date": "2026-08-27", "subject": "fix <script>alert(1)</script>"}]}))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_escapes_html_in_node_name(self):
        page = render_html(diff(nodes_added=[
            {"name": "<b>evil</b>", "kind": "cnr", "new": "1.0", "remote": None}]))
        self.assertNotIn("<b>evil</b>", page)

    def test_includes_compare_link_for_github_remote(self):
        page = render_html(diff(core={"old": "aaa", "new": "bbb", "commits": [],
                                      "remote": "https://github.com/Comfy-Org/ComfyUI"}))
        self.assertIn("https://github.com/Comfy-Org/ComfyUI/compare/aaa...bbb", page)

    def test_shows_cnr_node_version_transition(self):
        page = render_html(diff(nodes_changed=[
            {"name": "ComfyUI-Crystools", "kind": "cnr", "old": "1.27.4",
             "new": "1.28.0", "remote": None}]))
        self.assertIn("ComfyUI-Crystools", page)
        self.assertIn("1.27.4", page)
        self.assertIn("1.28.0", page)

    def test_marks_risky_package_row_with_a_class(self):
        page = render_html(diff(packages_changed=[
            {"name": "av", "old": "17.0.1", "new": "16.0.1", "risky": True}]))
        self.assertIn('class="pkg risky"', page)

    def test_ordinary_package_row_is_not_marked_risky(self):
        page = render_html(diff(packages_changed=[
            {"name": "rich", "old": "13.0", "new": "14.0", "risky": False}]))
        self.assertNotIn('class="pkg risky"', page)

    def test_is_a_complete_html_document(self):
        page = render_html(diff())
        self.assertTrue(page.lstrip().startswith("<!doctype html>"))
        self.assertIn("</html>", page)

    def test_states_when_nothing_changed(self):
        self.assertIn("No changes", render_html(diff()))


class ConsoleLanguage(unittest.TestCase):
    def test_defaults_to_korean(self):
        self.assertIn("지난 리포트", render_console(diff()))

    def test_switches_to_english_on_request(self):
        self.assertIn("No changes", render_console(diff(), lang="en"))

    def test_unknown_language_falls_back_to_korean(self):
        self.assertIn("지난 리포트", render_console(diff(), lang="fr"))

    def test_pads_a_korean_label_for_double_width_glyphs(self):
        out = render_console(diff(core={"old": "a", "new": "b", "remote": None,
                                        "commits": []}))
        self.assertIn("  코어        ", out)  # 4 columns of label + 8 spaces

    def test_reports_the_translation_count_when_there_was_one(self):
        out = render_console(diff(core={"old": "a", "new": "b", "remote": None,
                                        "commits": []}),
                             translated=7, model="qwen3")
        self.assertIn("7", out)
        self.assertIn("qwen3", out)

    def test_says_the_server_wanted_a_key(self):
        out = render_console(diff(core={"old": "a", "new": "b", "remote": None,
                                        "commits": []}), needs_key=True)
        self.assertIn("API", out)

    def test_says_nothing_about_translation_when_there_was_none(self):
        out = render_console(diff(core={"old": "a", "new": "b", "remote": None,
                                        "commits": []}))
        self.assertNotIn("번역", out)


def commit(subject, korean=None):
    entry = {"sha": "c1", "date": "2026-09-04", "subject": subject}
    if korean:
        entry["subject_ko"] = korean
    return entry


def core_with(*commits):
    return {"old": "aaa", "new": "bbb", "remote": None, "commits": list(commits)}


class RenderHtmlLanguage(unittest.TestCase):
    def test_opens_in_korean_by_default(self):
        self.assertIn('data-lang="ko"', render_html(diff()))

    def test_opens_in_english_when_asked(self):
        self.assertIn('data-lang="en"', render_html(diff(), lang="en"))

    def test_ships_both_languages_whichever_it_opens_in(self):
        for lang in ("ko", "en"):
            page = render_html(diff(nodes_changed=[
                {"name": "n", "kind": "cnr", "old": "1", "new": "2", "remote": None}]),
                lang=lang)
            self.assertIn("업데이트된 노드", page)
            self.assertIn("Updated nodes", page)

    def test_offers_a_button_for_each_language(self):
        page = render_html(diff())
        self.assertIn('data-set="ko"', page)
        self.assertIn('data-set="en"', page)

    def test_marks_the_language_the_page_opens_in(self):
        self.assertIn('class="langbtn on" data-set="ko"', render_html(diff()))
        self.assertIn('class="langbtn on" data-set="en"',
                      render_html(diff(), lang="en"))

    def test_binds_the_toggle_to_the_l_key(self):
        self.assertIn("toLowerCase()==='l'", render_html(diff()))

    def test_keeps_the_toggle_on_a_page_with_no_changes(self):
        page = render_html(diff())
        self.assertIn('data-set="en"', page)
        self.assertIn("바뀐 것이 없습니다", page)
        self.assertIn("No changes", page)

    def test_shows_a_translated_subject_beside_the_original(self):
        page = render_html(diff(core=core_with(
            commit("Support HDR video saving", "HDR 비디오 저장 지원"))))
        self.assertIn('<span class="lg-ko">HDR 비디오 저장 지원</span>', page)
        self.assertIn('<span class="lg-en">Support HDR video saving</span>', page)

    def test_writes_an_untranslated_subject_only_once(self):
        page = render_html(diff(core=core_with(commit("Fix a memory leak"))))
        self.assertEqual(page.count("Fix a memory leak"), 1)

    def test_escapes_a_translated_subject(self):
        page = render_html(diff(core=core_with(commit("x", "<b>번역</b>"))))
        self.assertNotIn("<b>번역</b>", page)
        self.assertIn("&lt;b&gt;", page)

    def test_notes_machine_translation_when_a_subject_was_translated(self):
        page = render_html(diff(core=core_with(commit("x", "엑스"))))
        self.assertIn("자동 번역", page)

    def test_says_nothing_about_machine_translation_otherwise(self):
        page = render_html(diff(core=core_with(commit("x"))))
        self.assertNotIn("자동 번역", page)

    def test_translates_the_package_state_words(self):
        page = render_html(diff(packages_added=[{"name": "rich", "new": "14.0"}],
                                packages_removed=[{"name": "old", "old": "1.0"}]))
        for text in ("(신규)", "(new)", "(제거됨)", "(removed)"):
            self.assertIn(text, page)

    def test_titles_the_page_in_both_languages(self):
        page = render_html(diff())
        self.assertIn('data-title-ko="ComfyUI 업데이트 리포트"', page)
        self.assertIn('data-title-en="ComfyUI update report"', page)


class ResolveOptions(unittest.TestCase):
    def resolve(self, argv=(), config=None, env=None):
        return update_report.resolve_options(list(argv), config or {}, env or {})

    def test_defaults_to_korean_with_translation_on(self):
        options = self.resolve()
        self.assertEqual(options["lang"], "ko")
        self.assertTrue(options["translate"])

    def test_reads_the_language_from_the_command_line(self):
        self.assertEqual(self.resolve(["--lang", "en"])["lang"], "en")

    def test_reads_the_language_from_the_config_file(self):
        self.assertEqual(self.resolve(config={"lang": "en"})["lang"], "en")

    def test_command_line_beats_the_config_file(self):
        self.assertEqual(self.resolve(["--lang", "ko"], {"lang": "en"})["lang"], "ko")

    def test_environment_beats_the_config_file(self):
        options = self.resolve(config={"lang": "ko"}, env={"COMFY_REPORT_LANG": "en"})
        self.assertEqual(options["lang"], "en")

    def test_no_translate_turns_translation_off(self):
        self.assertFalse(self.resolve(["--no-translate"])["translate"])

    def test_config_can_turn_translation_off(self):
        self.assertFalse(self.resolve(config={"translate": False})["translate"])

    def test_environment_can_turn_translation_off(self):
        options = self.resolve(env={"COMFY_REPORT_TRANSLATE": "0"})
        self.assertFalse(options["translate"])

    def test_reads_the_llm_endpoint_and_model(self):
        options = self.resolve(["--llm", "http://x/v1", "--llm-model", "m"])
        self.assertEqual(options["llm_url"], "http://x/v1")
        self.assertEqual(options["llm_model"], "m")

    def test_reads_the_llm_key_from_the_command_line(self):
        self.assertEqual(self.resolve(["--llm-key", "sk-1"])["llm_key"], "sk-1")

    def test_reads_the_llm_key_from_the_environment(self):
        options = self.resolve(env={"LM_API_TOKEN": "sk-2"})
        self.assertEqual(options["llm_key"], "sk-2")

    def test_reads_the_llm_key_from_the_config_file(self):
        self.assertEqual(self.resolve(config={"llm_key": "sk-3"})["llm_key"], "sk-3")

    def test_has_no_llm_key_by_default(self):
        self.assertIsNone(self.resolve()["llm_key"])

    def test_a_dangling_option_does_not_crash(self):
        self.assertEqual(self.resolve(["--lang"])["lang"], "ko")


class TranslateCommits(unittest.TestCase):
    def options(self, **over):
        base = {"lang": "ko", "translate": True, "llm_url": None, "llm_model": None}
        base.update(over)
        return base

    def test_does_nothing_when_translation_is_off(self):
        d = diff(core=core_with(commit("x")))
        self.assertEqual(
            update_report.translate_commits(d, self.options(translate=False), None),
            (0, None, False))
        self.assertNotIn("subject_ko", d["core"]["commits"][0])

    def test_survives_a_translator_that_explodes(self):
        def boom(*args, **kwargs):
            raise RuntimeError("no server, no mercy")

        d = diff(core=core_with(commit("x")))
        original = translate_module.apply_to_diff
        translate_module.apply_to_diff = boom
        try:
            self.assertEqual(update_report.translate_commits(d, self.options(), None),
                             (0, None, False))
        finally:
            translate_module.apply_to_diff = original


class LoadConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "config.json")

    def write(self, text):
        with io.open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_reads_a_settings_file(self):
        self.write('{"lang": "en"}')
        self.assertEqual(update_report.load_config(self.path), {"lang": "en"})

    def test_a_missing_file_is_no_settings(self):
        self.assertEqual(update_report.load_config(self.path), {})

    def test_broken_json_is_no_settings(self):
        self.write("{not json")
        self.assertEqual(update_report.load_config(self.path), {})

    def test_a_json_list_is_no_settings(self):
        self.write("[1, 2]")
        self.assertEqual(update_report.load_config(self.path), {})


class CollectPackages(unittest.TestCase):
    def dist(self, name, version):
        return type("D", (), {"metadata": {"Name": name}, "version": version})()

    def test_maps_distribution_name_to_version(self):
        self.assertEqual(collect_packages([self.dist("av", "17.0.1"),
                                           self.dist("numpy", "2.2.6")]),
                         {"av": "17.0.1", "numpy": "2.2.6"})

    def test_keeps_the_first_of_duplicate_distributions(self):
        self.assertEqual(collect_packages([self.dist("av", "17.0.1"),
                                           self.dist("av", "16.0.1")]), {"av": "17.0.1"})

    def test_skips_a_distribution_without_a_name(self):
        self.assertEqual(collect_packages([self.dist(None, "1.0"),
                                           self.dist("av", "17.0.1")]), {"av": "17.0.1"})

    def test_returns_empty_dict_when_nothing_is_installed(self):
        self.assertEqual(collect_packages([]), {})


class CollectNodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def make(self, name, git=False, pyproject=None):
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        if git:
            os.makedirs(os.path.join(path, ".git"), exist_ok=True)
        if pyproject is not None:
            with io.open(os.path.join(path, "pyproject.toml"), "w", encoding="utf-8") as fh:
                fh.write(pyproject)
        return path

    def fake_git(self, path):
        return {"sha": "sha-of-" + os.path.basename(path), "remote": "https://github.com/x/y"}

    def collect(self):
        return collect_nodes(self.root, git_reader=self.fake_git)

    def test_reads_version_of_a_registry_node(self):
        self.make("ComfyUI-Crystools", pyproject=PYPROJECT_VERSIONED)
        self.assertEqual(self.collect()["ComfyUI-Crystools"],
                         {"kind": "cnr", "version": "1.27.4", "remote": None})

    def test_reads_sha_of_a_git_node(self):
        self.make("was-node-suite", git=True)
        self.assertEqual(self.collect()["was-node-suite"],
                         {"kind": "git", "sha": "sha-of-was-node-suite",
                          "remote": "https://github.com/x/y"})

    def test_prefers_git_over_pyproject_when_both_exist(self):
        self.make("ComfyUI-Easy-Use", git=True, pyproject=PYPROJECT_VERSIONED)
        self.assertEqual(self.collect()["ComfyUI-Easy-Use"]["kind"], "git")

    def test_reads_repository_url_of_a_registry_node(self):
        self.make("nodepack", pyproject=PYPROJECT_WITH_URL)
        self.assertEqual(self.collect()["nodepack"]["remote"], "https://github.com/a/b")

    def test_keeps_a_registry_node_that_has_no_pyproject(self):
        self.make("mystery-node")
        self.assertEqual(self.collect()["mystery-node"],
                         {"kind": "cnr", "version": None, "remote": None})

    def test_skips_pycache(self):
        self.make("__pycache__")
        self.assertNotIn("__pycache__", self.collect())

    def test_skips_loose_files(self):
        with io.open(os.path.join(self.root, "websocket_image_save.py"), "w") as fh:
            fh.write("x")
        self.assertEqual(self.collect(), {})

    def test_returns_empty_dict_for_a_missing_directory(self):
        self.assertEqual(collect_nodes(os.path.join(self.root, "nope"),
                                       git_reader=self.fake_git), {})


class FindComfyDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def make(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def test_finds_custom_nodes_in_the_starting_directory(self):
        self.make("custom_nodes")
        self.assertEqual(find_comfy_dir(self.root), self.root)

    def test_finds_a_comfyui_subdirectory(self):
        self.make("ComfyUI", "custom_nodes")
        self.assertEqual(find_comfy_dir(self.root), os.path.join(self.root, "ComfyUI"))

    def test_walks_up_from_a_nested_starting_directory(self):
        self.make("ComfyUI", "custom_nodes")
        start = self.make("_update-report")
        self.assertEqual(find_comfy_dir(start), os.path.join(self.root, "ComfyUI"))

    def test_prefers_the_starting_directory_over_a_parent(self):
        self.make("custom_nodes")
        self.make("ComfyUI", "custom_nodes")
        nested = os.path.join(self.root, "ComfyUI")
        self.assertEqual(find_comfy_dir(nested), nested)

    def test_returns_none_when_no_comfyui_is_near(self):
        self.assertIsNone(find_comfy_dir(self.make("lonely")))

    def test_stops_climbing_at_the_given_depth(self):
        self.make("custom_nodes")
        deep = self.make("a", "b", "c", "d")
        self.assertIsNone(find_comfy_dir(deep, max_up=2))


class EnrichCommits(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def reader(self, path, old, new):
        self.calls.append((os.path.basename(path), old, new))
        return [{"sha": "c%d" % i, "date": "2026-08-27", "subject": "s%d" % i}
                for i in range(3)]

    def test_attaches_commits_to_the_core_entry(self):
        d = diff(core={"old": "aaa", "new": "bbb", "remote": None})
        enrich_commits(d, os.path.join("X", "ComfyUI"), log_reader=self.reader)
        self.assertEqual(len(d["core"]["commits"]), 3)

    def test_reads_the_core_log_from_the_comfyui_directory(self):
        d = diff(core={"old": "aaa", "new": "bbb", "remote": None})
        enrich_commits(d, os.path.join("X", "ComfyUI"), log_reader=self.reader)
        self.assertEqual(self.calls, [("ComfyUI", "aaa", "bbb")])

    def test_attaches_commits_to_a_changed_git_node(self):
        d = diff(nodes_changed=[{"name": "was-node-suite", "kind": "git",
                                 "old": "1f2a3b4", "new": "9c8d7e6", "remote": None}])
        enrich_commits(d, "COMFY", log_reader=self.reader)
        self.assertEqual(len(d["nodes_changed"][0]["commits"]), 3)

    def test_reads_a_node_log_from_its_custom_nodes_directory(self):
        d = diff(nodes_changed=[{"name": "was-node-suite", "kind": "git",
                                 "old": "1f2a3b4", "new": "9c8d7e6", "remote": None}])
        enrich_commits(d, "COMFY", log_reader=self.reader)
        self.assertEqual(self.calls, [("was-node-suite", "1f2a3b4", "9c8d7e6")])

    def test_leaves_a_registry_node_without_commits(self):
        d = diff(nodes_changed=[{"name": "ComfyUI-Crystools", "kind": "cnr",
                                 "old": "1.27.4", "new": "1.28.0", "remote": None}])
        enrich_commits(d, "COMFY", log_reader=self.reader)
        self.assertNotIn("commits", d["nodes_changed"][0])

    def test_truncates_to_the_limit_and_counts_the_rest(self):
        many = [{"sha": "c%d" % i, "date": "d", "subject": "s"} for i in range(25)]
        d = diff(core={"old": "a", "new": "b", "remote": None})
        enrich_commits(d, "COMFY", log_reader=lambda p, o, n: many, limit=20)
        self.assertEqual(len(d["core"]["commits"]), 20)
        self.assertEqual(d["core"]["commits_omitted"], 5)

    def test_records_no_omission_when_under_the_limit(self):
        d = diff(core={"old": "a", "new": "b", "remote": None})
        enrich_commits(d, "COMFY", log_reader=self.reader, limit=20)
        self.assertEqual(d["core"]["commits_omitted"], 0)

    def test_falls_back_to_an_empty_list_when_the_log_fails(self):
        def boom(path, old, new):
            raise RuntimeError("fatal: bad revision")
        d = diff(core={"old": "a", "new": "b", "remote": None})
        enrich_commits(d, "COMFY", log_reader=boom)
        self.assertEqual(d["core"]["commits"], [])

    def test_does_nothing_when_there_is_no_core_change(self):
        d = diff(nodes_added=[{"name": "x", "kind": "cnr", "new": "1.0", "remote": None}])
        enrich_commits(d, "COMFY", log_reader=self.reader)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
