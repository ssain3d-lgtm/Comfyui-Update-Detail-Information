# -*- coding: utf-8 -*-
"""Tests for i18n.py -- run with: python test_i18n.py"""
import os
import re
import sys
import unittest

# An embedded python (._pth) does not put the script directory on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n
from i18n import LANGS, STRINGS, both, normalize, pad, t, width

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class Table(unittest.TestCase):
    def test_every_key_carries_two_languages(self):
        for key, pair in STRINGS.items():
            self.assertEqual(len(pair), 2, key)

    def test_no_string_is_empty(self):
        for key, pair in STRINGS.items():
            for text in pair:
                self.assertTrue(text.strip(), key)

    def test_a_korean_string_is_actually_korean(self):
        # Not every string can be (ComfyUI stays ComfyUI), but the prose ones must.
        for key in ("no_changes", "c_baseline", "commits_none"):
            self.assertRegex(STRINGS[key][0], "[가-힣]", key)

    def test_placeholders_match_between_languages(self):
        for key, (ko, en) in STRINGS.items():
            self.assertEqual(set(_PLACEHOLDER_RE.findall(ko)),
                             set(_PLACEHOLDER_RE.findall(en)), key)

    def test_every_language_has_a_button_label(self):
        for lang in LANGS:
            self.assertTrue(i18n.LANG_LABELS[lang])


class Translate(unittest.TestCase):
    def test_returns_korean_by_default(self):
        self.assertEqual(t("title"), "ComfyUI 업데이트 리포트")

    def test_returns_english_on_request(self):
        self.assertEqual(t("title", "en"), "ComfyUI update report")

    def test_fills_placeholders(self):
        self.assertEqual(t("c_commits", "en", n=4), "4 commits")

    def test_both_returns_korean_first(self):
        self.assertEqual(both("title"), ("ComfyUI 업데이트 리포트",
                                         "ComfyUI update report"))

    def test_both_fills_the_same_placeholders_twice(self):
        korean, english = both("sec_packages", n=7)
        self.assertIn("7", korean)
        self.assertIn("7", english)

    def test_an_unknown_key_is_a_programming_error(self):
        with self.assertRaises(KeyError):
            t("no-such-key")


class Normalize(unittest.TestCase):
    def test_keeps_a_known_language(self):
        self.assertEqual(normalize("en"), "en")

    def test_is_case_insensitive(self):
        self.assertEqual(normalize("EN"), "en")

    def test_accepts_common_spellings(self):
        for spelling in ("kr", "Korean", "한국어", "한글"):
            self.assertEqual(normalize(spelling), "ko")
        for spelling in ("eng", "English", "영어"):
            self.assertEqual(normalize(spelling), "en")

    def test_unknown_falls_back_to_korean(self):
        self.assertEqual(normalize("fr"), "ko")

    def test_none_falls_back_to_korean(self):
        self.assertEqual(normalize(None), "ko")


class ConsoleWidth(unittest.TestCase):
    def test_ascii_is_one_column_per_character(self):
        self.assertEqual(width("core"), 4)

    def test_hangul_is_two_columns_per_character(self):
        self.assertEqual(width("코어"), 4)

    def test_mixed_text_adds_up(self):
        self.assertEqual(width("코어 core"), 4 + 1 + 4)

    def test_pads_to_a_column_count(self):
        self.assertEqual(width(pad("패키지", 12)), 12)
        self.assertEqual(width(pad("packages", 12)), 12)

    def test_does_not_truncate_something_too_wide(self):
        self.assertEqual(pad("packages", 4), "packages")


if __name__ == "__main__":
    unittest.main(verbosity=2)
