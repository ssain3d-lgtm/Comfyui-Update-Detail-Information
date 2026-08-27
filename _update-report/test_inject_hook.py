# -*- coding: utf-8 -*-
"""Tests for inject_hook.py -- run with: python test_inject_hook.py"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inject_hook import HOOK_MARKER, find_update_bats, inject_hook_text

CRLF = chr(13) + chr(10)


def bat(*lines):
    return CRLF.join(lines) + CRLF


def hook_line_of(text):
    return [l for l in text.split(CRLF) if HOOK_MARKER in l and not l.startswith("::")][0]


class InjectHookText(unittest.TestCase):
    def test_inserts_the_hook_above_the_start_call(self):
        out = inject_hook_text(bat("@echo off", 'call "Start ComfyUI.bat"'))
        lines = out.split(CRLF)
        self.assertLess(lines.index(hook_line_of(out)),
                        lines.index('call "Start ComfyUI.bat"'))

    def test_keeps_the_start_call(self):
        out = inject_hook_text(bat("@echo off", 'call "Start ComfyUI.bat"'))
        self.assertIn('call "Start ComfyUI.bat"', out)

    def test_is_idempotent(self):
        once = inject_hook_text(bat("@echo off", 'call "Start ComfyUI.bat"'))
        self.assertEqual(inject_hook_text(once), once)

    def test_returns_none_when_there_is_no_anchor_and_no_fallback(self):
        self.assertIsNone(inject_hook_text(bat("@echo off", "echo nothing to anchor to")))

    def test_appends_at_the_end_when_fallback_is_allowed(self):
        out = inject_hook_text(bat("@echo off", "git pull"), fallback_append=True)
        lines = [l for l in out.split(CRLF) if l]
        self.assertEqual(lines[-1], hook_line_of(out))

    def test_refuses_to_append_below_a_subroutine_label(self):
        # Appending past a label would land the hook inside that subroutine,
        # so it would run on every call to it instead of once at the end.
        text = bat("@echo off", "git pull", "goto :EOF", ":rename_files", 'ren "%~1" "%~2"')
        self.assertIsNone(inject_hook_text(text, fallback_append=True))

    def test_treats_a_double_colon_comment_as_ordinary_text(self):
        out = inject_hook_text(bat("@echo off", ":: just a comment", "git pull"),
                               fallback_append=True)
        self.assertIsNotNone(out)

    def test_inserts_above_a_trailing_pause_rather_than_after_it(self):
        out = inject_hook_text(bat("@echo off", "git pull", "pause"), fallback_append=True)
        lines = [l for l in out.split(CRLF) if l]
        self.assertEqual(lines[-1], "pause")
        self.assertLess(lines.index(hook_line_of(out)), lines.index("pause"))

    def test_finds_the_anchor_in_a_file_that_uses_bare_newlines(self):
        text = "@echo off\ncall \"Start ComfyUI.bat\"\n"
        out = inject_hook_text(text)
        lines = out.split("\n")
        self.assertLess(lines.index([l for l in lines if HOOK_MARKER in l
                                     and not l.startswith("::")][0]),
                        lines.index('call "Start ComfyUI.bat"'))

    def test_inserts_above_a_trailing_pause_in_a_bare_newline_file(self):
        out = inject_hook_text("@echo off\ngit pull\npause\n", fallback_append=True)
        lines = [l for l in out.split("\n") if l]
        self.assertEqual(lines[-1], "pause")

    def test_does_not_introduce_crlf_into_a_bare_newline_file(self):
        out = inject_hook_text("@echo off\ngit pull\n", fallback_append=True)
        self.assertNotIn(chr(13), out)

    def test_preserves_crlf_line_endings(self):
        out = inject_hook_text(bat("@echo off", 'call "Start ComfyUI.bat"'))
        self.assertNotIn(chr(10), out.replace(CRLF, ""))

    def test_matches_the_anchor_regardless_of_case_and_spacing(self):
        out = inject_hook_text(bat("@echo off", 'CALL   "Start ComfyUI.bat"'))
        self.assertIn(HOOK_MARKER, out)

    def test_anchors_on_the_last_start_call_when_several_exist(self):
        out = inject_hook_text(bat('call "Start ComfyUI.bat"', "echo middle",
                                   'call "Start ComfyUI.bat"'))
        lines = out.split(CRLF)
        self.assertGreater(lines.index(hook_line_of(out)), lines.index("echo middle"))
        self.assertEqual(
            len([l for l in lines if HOOK_MARKER in l and not l.startswith("::")]), 1)

    def test_points_at_the_parent_directory_when_a_prefix_is_given(self):
        out = inject_hook_text(bat("@echo off", "git pull"), rel="..", fallback_append=True)
        self.assertIn('"%~dp0..\\' + HOOK_MARKER + '"', out)

    def test_points_at_its_own_directory_without_a_prefix(self):
        out = inject_hook_text(bat("@echo off", "git pull"), fallback_append=True)
        self.assertIn('"%~dp0' + HOOK_MARKER + '"', out)

    def test_stays_ascii_so_any_codepage_survives_the_rewrite(self):
        inject_hook_text(bat("@echo off", "git pull"), fallback_append=True).encode("ascii")


class FindUpdateBats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def make(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="ascii") as fh:
            fh.write("@echo off" + CRLF)
        return path

    def names(self):
        return sorted(os.path.basename(b["path"]) for b in find_update_bats(self.root))

    def test_finds_an_update_bat_in_the_root(self):
        self.make("Update ComfyUI.bat")
        self.assertEqual(self.names(), ["Update ComfyUI.bat"])

    def test_finds_an_update_bat_inside_the_update_directory(self):
        self.make("update", "update_comfyui.bat")
        self.assertEqual(self.names(), ["update_comfyui.bat"])

    def test_reports_a_parent_prefix_for_a_bat_in_the_update_directory(self):
        self.make("update", "update_comfyui.bat")
        self.assertEqual(find_update_bats(self.root)[0]["rel"], "..")

    def test_reports_no_prefix_for_a_bat_in_the_root(self):
        self.make("Update ComfyUI.bat")
        self.assertEqual(find_update_bats(self.root)[0]["rel"], "")

    def test_prefers_root_bats_over_the_ones_they_call(self):
        # A root updater usually calls update\update_comfyui.bat itself; hooking
        # both would run the report twice and the second run would see no diff.
        self.make("Update ComfyUI.bat")
        self.make("update", "update_comfyui.bat")
        self.assertEqual(self.names(), ["Update ComfyUI.bat"])

    def test_ignores_a_bat_that_does_not_update_anything(self):
        self.make("Start ComfyUI.bat")
        self.make("run_nvidia_gpu.bat")
        self.assertEqual(self.names(), [])

    def test_ignores_the_report_launcher_itself(self):
        self.make(HOOK_MARKER)
        self.assertEqual(self.names(), [])

    def test_ignores_backup_copies(self):
        self.make("Update ComfyUI.bat.bak-hook")
        self.assertEqual(self.names(), [])

    def test_matches_the_name_case_insensitively(self):
        self.make("UPDATE_COMFYUI.BAT")
        self.assertEqual(self.names(), ["UPDATE_COMFYUI.BAT"])

    def test_returns_nothing_for_a_missing_directory(self):
        self.assertEqual(find_update_bats(os.path.join(self.root, "nope")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
