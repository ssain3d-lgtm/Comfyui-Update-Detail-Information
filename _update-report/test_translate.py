# -*- coding: utf-8 -*-
"""Tests for translate.py -- run with: python test_translate.py

No network: every test drives the translator through a fake http callable.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

# An embedded python (._pth) does not put the script directory on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import translate
from translate import (Translator, apply_to_diff, discover, load_cache,
                       needs_translation, parse_reply, save_cache)

NL = chr(10)


def reply(*items):
    return {"choices": [{"message": {"content": json.dumps(list(items),
                                                           ensure_ascii=False)}}]}


class FakeHttp(object):
    """A local LLM that answers /models and echoes a canned translation."""

    def __init__(self, answers=None, models=("m1",), fail_on=None):
        self.answers = answers or {}
        self.models = models
        self.fail_on = fail_on or ()
        self.calls = []
        self.keys = []
        self.batches = []

    def __call__(self, url, payload=None, timeout=None, key=None):
        self.calls.append(url)
        self.keys.append(key)
        for fragment in self.fail_on:
            if fragment in url:
                raise IOError("refused")
        if url.endswith("/models"):
            return {"data": [{"id": name} for name in self.models]}
        subjects = json.loads(payload["messages"][1]["content"])
        self.batches.append(subjects)
        return reply(*[self.answers.get(s, "번역:" + s) for s in subjects])


class ParseReply(unittest.TestCase):
    def test_reads_a_json_array(self):
        self.assertEqual(parse_reply('["가", "나"]', 2), ["가", "나"])

    def test_reads_a_fenced_json_array(self):
        fenced = "```json" + NL + '["가"]' + NL + "```"
        self.assertEqual(parse_reply(fenced, 1), ["가"])

    def test_reads_an_array_wrapped_in_chatter(self):
        self.assertEqual(parse_reply('Sure!' + NL + '["가"]' + NL + 'Done.', 1), ["가"])

    def test_reads_a_numbered_list(self):
        self.assertEqual(parse_reply("1. 가" + NL + "2. 나", 2), ["가", "나"])

    def test_strips_quotes_from_a_numbered_list(self):
        self.assertEqual(parse_reply('1. "가"', 1), ["가"])

    def test_rejects_the_wrong_number_of_items(self):
        self.assertIsNone(parse_reply('["가"]', 2))

    def test_rejects_non_strings(self):
        self.assertIsNone(parse_reply("[1, 2]", 2))

    def test_rejects_prose(self):
        self.assertIsNone(parse_reply("I cannot do that", 1))

    def test_rejects_nothing(self):
        self.assertIsNone(parse_reply("", 1))
        self.assertIsNone(parse_reply(None, 1))

    def test_trims_whitespace_around_items(self):
        self.assertEqual(parse_reply('["  가  "]', 1), ["가"])


class NeedsTranslation(unittest.TestCase):
    def test_english_subject_qualifies(self):
        self.assertTrue(needs_translation("Fix a memory leak"))

    def test_already_korean_does_not(self):
        self.assertFalse(needs_translation("메모리 누수 수정"))

    def test_empty_does_not(self):
        self.assertFalse(needs_translation(""))
        self.assertFalse(needs_translation("   "))
        self.assertFalse(needs_translation(None))

    def test_a_subject_without_words_does_not(self):
        self.assertFalse(needs_translation("1.2.3 -> 1.3.0"))


class Discover(unittest.TestCase):
    def test_returns_the_first_endpoint_that_answers(self):
        http = FakeHttp(models=("qwen",))
        base, model = discover(["http://a/v1", "http://b/v1"], None, http)
        self.assertEqual((base, model), ("http://a/v1", "qwen"))

    def test_skips_an_endpoint_that_refuses(self):
        http = FakeHttp(models=("qwen",), fail_on=["http://a/"])
        base, _ = discover(["http://a/v1", "http://b/v1"], None, http)
        self.assertEqual(base, "http://b/v1")

    def test_keeps_an_explicitly_chosen_model(self):
        http = FakeHttp(models=("qwen",))
        _, model = discover(["http://a/v1"], "mine", http)
        self.assertEqual(model, "mine")

    def test_gives_up_when_nothing_answers(self):
        http = FakeHttp(fail_on=["http"])
        self.assertEqual(discover(["http://a/v1"], None, http), (None, None))

    def test_ignores_an_endpoint_with_no_models(self):
        def empty(url, payload=None, timeout=None, key=None):
            return {"data": []}
        self.assertEqual(discover(["http://a/v1"], None, empty), (None, None))

    def test_tolerates_a_trailing_slash(self):
        http = FakeHttp()
        base, _ = discover(["http://a/v1/"], None, http)
        self.assertEqual(base, "http://a/v1")


class Cache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "translations.json")

    def write(self, text):
        with io.open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_round_trips(self):
        save_cache(self.path, {"fix": "수정"})
        self.assertEqual(load_cache(self.path), {"fix": "수정"})

    def test_a_missing_file_is_an_empty_cache(self):
        self.assertEqual(load_cache(self.path), {})

    def test_broken_json_is_an_empty_cache(self):
        self.write("{not json")
        self.assertEqual(load_cache(self.path), {})

    def test_an_older_format_is_ignored(self):
        self.write('{"version": 0, "entries": {"fix": "수정"}}')
        self.assertEqual(load_cache(self.path), {})

    def test_non_string_values_are_dropped(self):
        self.write('{"version": 1, "entries": {"fix": 5, "ok": "좋음"}}')
        self.assertEqual(load_cache(self.path), {"ok": "좋음"})


class TranslateSubjects(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cache = os.path.join(self.dir, "translations.json")

    def translator(self, http, **over):
        options = {"cache_path": self.cache, "endpoints": ["http://a/v1"], "http": http}
        options.update(over)
        return Translator(**options)

    def test_translates_a_subject(self):
        http = FakeHttp({"fix leak": "누수 수정"})
        self.assertEqual(self.translator(http).translate(["fix leak"]),
                         {"fix leak": "누수 수정"})

    def test_writes_what_it_learned_to_the_cache(self):
        self.translator(FakeHttp({"fix leak": "누수 수정"})).translate(["fix leak"])
        self.assertEqual(load_cache(self.cache), {"fix leak": "누수 수정"})

    def test_a_cached_subject_costs_no_request(self):
        save_cache(self.cache, {"fix leak": "누수 수정"})
        http = FakeHttp()
        self.assertEqual(self.translator(http).translate(["fix leak"]),
                         {"fix leak": "누수 수정"})
        self.assertEqual(http.calls, [])

    def test_asks_only_about_the_uncached_subjects(self):
        save_cache(self.cache, {"a": "가"})
        http = FakeHttp()
        self.translator(http).translate(["a", "b"])
        self.assertEqual(http.batches, [["b"]])

    def test_asks_once_per_batch(self):
        http = FakeHttp()
        self.translator(http, batch=2).translate(["a", "b", "c"])
        self.assertEqual(http.batches, [["a", "b"], ["c"]])

    def test_asks_about_a_repeated_subject_once(self):
        http = FakeHttp()
        self.translator(http).translate(["a", "a"])
        self.assertEqual(http.batches, [["a"]])

    def test_skips_a_subject_that_is_already_korean(self):
        http = FakeHttp()
        self.assertEqual(self.translator(http).translate(["이미 한글"]), {})
        self.assertEqual(http.calls, [])

    def test_no_server_means_no_translation_and_no_crash(self):
        http = FakeHttp(fail_on=["http"])
        self.assertEqual(self.translator(http).translate(["a"]), {})

    def test_a_dead_server_mid_run_keeps_the_earlier_batches(self):
        class DiesAfterOne(FakeHttp):
            def __call__(self, url, payload=None, timeout=None, key=None):
                if len(self.batches) >= 1 and not url.endswith("/models"):
                    raise IOError("gone")
                return FakeHttp.__call__(self, url, payload, timeout, key)

        http = DiesAfterOne({"a": "가"})
        done = self.translator(http, batch=1).translate(["a", "b"])
        self.assertEqual(done, {"a": "가"})

    def test_an_unparsable_answer_is_skipped(self):
        def rambles(url, payload=None, timeout=None, key=None):
            if url.endswith("/models"):
                return {"data": [{"id": "m"}]}
            return {"choices": [{"message": {"content": "I refuse"}}]}
        self.assertEqual(self.translator(rambles).translate(["a"]), {})

    def test_an_empty_response_is_skipped(self):
        def empty(url, payload=None, timeout=None, key=None):
            if url.endswith("/models"):
                return {"data": [{"id": "m"}]}
            return {"choices": []}
        self.assertEqual(self.translator(empty).translate(["a"]), {})

    def test_an_answer_identical_to_the_original_is_not_stored(self):
        http = FakeHttp({"a": "a"})
        self.assertEqual(self.translator(http).translate(["a"]), {})
        self.assertEqual(load_cache(self.cache), {})

    def test_stops_when_the_time_budget_runs_out(self):
        ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])
        http = FakeHttp()
        translator = self.translator(http, batch=1, budget=10.0,
                                     clock=lambda: next(ticks))
        translator.translate(["a", "b", "c"])
        self.assertEqual(http.batches, [["a"]])

    def test_counts_what_it_translated(self):
        translator = self.translator(FakeHttp())
        translator.translate(["a", "b"])
        self.assertEqual(translator.translated, 2)

    def test_remembers_which_model_answered(self):
        translator = self.translator(FakeHttp(models=("qwen3",)))
        translator.translate(["a"])
        self.assertEqual(translator.used_model, "qwen3")

    def test_works_without_a_cache_file(self):
        http = FakeHttp({"a": "가"})
        translator = Translator(cache_path=None, endpoints=["http://a/v1"], http=http)
        self.assertEqual(translator.translate(["a"]), {"a": "가"})


class ApiKey(unittest.TestCase):
    def test_sends_the_key_on_every_request(self):
        http = FakeHttp()
        translator = Translator(endpoints=["http://a/v1"], key="sk-1", http=http)
        translator.translate(["a"])
        self.assertEqual(set(http.keys), {"sk-1"})

    def test_sends_nothing_when_there_is_no_key(self):
        http = FakeHttp()
        Translator(endpoints=["http://a/v1"], http=http).translate(["a"])
        self.assertEqual(set(http.keys), {None})

    def test_notes_a_server_that_demands_a_key(self):
        def unauthorized(url, payload=None, timeout=None, key=None):
            error = IOError("401")
            error.status = 401
            raise error

        notes = []
        self.assertEqual(discover(["http://a/v1"], None, unauthorized, None, notes),
                         (None, None))
        self.assertEqual(notes, ["auth"])

    def test_does_not_call_a_refused_connection_an_auth_problem(self):
        notes = []
        discover(["http://a/v1"], None, FakeHttp(fail_on=["http"]), None, notes)
        self.assertEqual(notes, [])

    def test_the_translator_reports_that_a_key_was_wanted(self):
        def unauthorized(url, payload=None, timeout=None, key=None):
            error = IOError("403")
            error.code = 403
            raise error

        translator = Translator(endpoints=["http://a/v1"], http=unauthorized)
        self.assertEqual(translator.translate(["a"]), {})
        self.assertTrue(translator.needs_key)

    def test_a_reachable_server_is_not_an_auth_problem(self):
        translator = Translator(endpoints=["http://a/v1"], http=FakeHttp())
        translator.translate(["a"])
        self.assertFalse(translator.needs_key)

    def test_reads_a_key_from_the_environment_in_order(self):
        self.assertEqual(translate.key_from_env({"OPENAI_API_KEY": "sk-o"}), "sk-o")
        self.assertEqual(translate.key_from_env(
            {"OPENAI_API_KEY": "sk-o", "LM_API_TOKEN": "sk-l"}), "sk-l")
        self.assertEqual(translate.key_from_env(
            {"LM_API_TOKEN": "sk-l", "COMFY_REPORT_LLM_KEY": "sk-c"}), "sk-c")

    def test_no_key_in_the_environment(self):
        self.assertIsNone(translate.key_from_env({}))
        self.assertIsNone(translate.key_from_env({"OPENAI_API_KEY": "  "}))

    def test_reads_the_status_off_either_attribute(self):
        status_error = IOError("x")
        status_error.status = 401
        code_error = IOError("x")
        code_error.code = 403
        self.assertEqual(translate.status_of(status_error), 401)
        self.assertEqual(translate.status_of(code_error), 403)
        self.assertIsNone(translate.status_of(IOError("plain")))


class ApplyToDiff(unittest.TestCase):
    def diff(self, core=None, nodes=None):
        return {"core": core, "nodes_changed": nodes or []}

    def test_attaches_korean_to_a_core_commit(self):
        d = self.diff(core={"commits": [{"subject": "a"}]})
        apply_to_diff(d, Translator(endpoints=["http://a/v1"],
                                    http=FakeHttp({"a": "가"})))
        self.assertEqual(d["core"]["commits"][0]["subject_ko"], "가")

    def test_attaches_korean_to_a_node_commit(self):
        d = self.diff(nodes=[{"commits": [{"subject": "a"}]}])
        apply_to_diff(d, Translator(endpoints=["http://a/v1"],
                                    http=FakeHttp({"a": "가"})))
        self.assertEqual(d["nodes_changed"][0]["commits"][0]["subject_ko"], "가")

    def test_leaves_an_untranslated_commit_alone(self):
        d = self.diff(core={"commits": [{"subject": "a"}]})
        apply_to_diff(d, Translator(endpoints=["http://a/v1"],
                                    http=FakeHttp(fail_on=["http"])))
        self.assertNotIn("subject_ko", d["core"]["commits"][0])

    def test_a_diff_without_commits_asks_nothing(self):
        http = FakeHttp()
        d = self.diff(nodes=[{"name": "n"}])
        self.assertEqual(apply_to_diff(d, Translator(http=http)), 0)
        self.assertEqual(http.calls, [])

    def test_returns_the_number_translated(self):
        d = self.diff(core={"commits": [{"subject": "a"}, {"subject": "b"}]})
        count = apply_to_diff(d, Translator(endpoints=["http://a/v1"], http=FakeHttp()))
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
