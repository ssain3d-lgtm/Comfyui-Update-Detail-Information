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
from translate import (GoogleTranslator, LLMTranslator, apply_to_diff, discover,
                       google_url, load_cache, parse_google,
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
        return LLMTranslator(**options)

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
        translator = LLMTranslator(cache_path=None, endpoints=["http://a/v1"], http=http)
        self.assertEqual(translator.translate(["a"]), {"a": "가"})


class ApiKey(unittest.TestCase):
    def test_sends_the_key_on_every_request(self):
        http = FakeHttp()
        translator = LLMTranslator(endpoints=["http://a/v1"], key="sk-1", http=http)
        translator.translate(["a"])
        self.assertEqual(set(http.keys), {"sk-1"})

    def test_sends_nothing_when_there_is_no_key(self):
        http = FakeHttp()
        LLMTranslator(endpoints=["http://a/v1"], http=http).translate(["a"])
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

        translator = LLMTranslator(endpoints=["http://a/v1"], http=unauthorized)
        self.assertEqual(translator.translate(["a"]), {})
        self.assertTrue(translator.needs_key)

    def test_a_reachable_server_is_not_an_auth_problem(self):
        translator = LLMTranslator(endpoints=["http://a/v1"], http=FakeHttp())
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


class Reachability(unittest.TestCase):
    def test_a_silent_server_counts_as_unreachable(self):
        translator = LLMTranslator(endpoints=["http://a/v1"], http=FakeHttp(fail_on=["http"]))
        translator.translate(["a"])
        self.assertTrue(translator.unreachable)

    def test_a_server_that_answered_does_not(self):
        translator = LLMTranslator(endpoints=["http://a/v1"], http=FakeHttp())
        translator.translate(["a"])
        self.assertFalse(translator.unreachable)

    def test_nothing_to_ask_is_not_unreachable(self):
        translator = LLMTranslator(endpoints=["http://a/v1"], http=FakeHttp(fail_on=["http"]))
        translator.translate(["이미 한글"])
        self.assertFalse(translator.unreachable)


# ---------------------------------------------------------------- google --

def gtx_body(text, answers):
    """A translate_a/single reply: one segment per input line, newline kept."""
    lines = text.split(NL)
    segments = []
    for index, line in enumerate(lines):
        tail = NL if index < len(lines) - 1 else ""
        korean = answers.get(line, "번역:" + line)
        segments.append([korean + tail, line + tail, None, None, 10])
    return [segments, None, "en", None, None, None, None, []]


class FakeGoogle(object):
    """translate.googleapis.com, minus the network."""

    def __init__(self, answers=None, merge_lines=False, blocked=False):
        self.answers = answers or {}
        self.merge_lines = merge_lines
        self.blocked = blocked
        self.texts = []
        self.keys = []

    def __call__(self, url, payload=None, timeout=None, key=None):
        self.keys.append(key)
        if self.blocked:
            raise ValueError("a 'Sorry...' HTML page is not JSON")
        query = translate.urlparse.parse_qs(translate.urlparse.urlsplit(url).query)
        text = query["q"][0]
        self.texts.append(text)
        body = gtx_body(text, self.answers)
        if self.merge_lines:
            # Google occasionally folds a line break into a space.
            merged = "".join(seg[0] for seg in body[0]).replace(NL, " ")
            body = [[[merged, text, None, None, 10]], None, "en"]
        return body


class GoogleUrl(unittest.TestCase):
    def test_targets_korean_through_the_gtx_client(self):
        url = google_url("Fix a leak")
        self.assertTrue(url.startswith(translate.GOOGLE_ENDPOINT + "?"))
        for fragment in ("client=gtx", "tl=ko", "dt=t", "sl=auto"):
            self.assertIn(fragment, url)

    def test_encodes_the_text(self):
        self.assertIn("q=Fix+a+leak+%26+more", google_url("Fix a leak & more"))


class ParseGoogle(unittest.TestCase):
    def test_joins_the_segments(self):
        body = [[["누수 수정. ", "Fix leak. ", None, None, 10],
                 ["끝", "Done", None, None, 10]], None, "en"]
        self.assertEqual(parse_google(body), "누수 수정. 끝")

    def test_keeps_line_breaks_between_segments(self):
        self.assertEqual(parse_google(gtx_body("a" + NL + "b", {"a": "가", "b": "나"})),
                         "가" + NL + "나")

    def test_accepts_the_bare_string_shape(self):
        self.assertEqual(parse_google(["번역"]), "번역")

    def test_rejects_anything_else(self):
        for junk in (None, {}, [], [None], [[]], "text", [[[1]]]):
            self.assertIsNone(parse_google(junk), repr(junk))


class GoogleTranslate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cache = os.path.join(self.dir, "translations.json")

    def translator(self, http, **over):
        options = {"cache_path": self.cache, "http": http}
        options.update(over)
        return GoogleTranslator(**options)

    def test_translates_a_subject(self):
        http = FakeGoogle({"Fix leak": "누수 수정"})
        self.assertEqual(self.translator(http).translate(["Fix leak"]),
                         {"Fix leak": "누수 수정"})

    def test_sends_a_batch_as_one_request_line_by_line(self):
        http = FakeGoogle()
        self.translator(http).translate(["a", "b", "c"])
        self.assertEqual(http.texts, ["a" + NL + "b" + NL + "c"])

    def test_falls_back_to_one_request_per_subject_when_lines_get_merged(self):
        http = FakeGoogle({"a": "가", "b": "나"}, merge_lines=True)
        done = self.translator(http).translate(["a", "b"])
        self.assertEqual(http.texts, ["a" + NL + "b", "a", "b"])
        self.assertEqual(done, {"a": "가", "b": "나"})

    def test_splits_a_batch_by_subject_count(self):
        http = FakeGoogle()
        self.translator(http, batch=2).translate(["a", "b", "c"])
        self.assertEqual(http.texts, ["a" + NL + "b", "c"])

    def test_splits_a_batch_by_size(self):
        http = FakeGoogle()
        self.translator(http, max_chars=12).translate(["a" * 8, "b" * 8, "c"])
        self.assertEqual(http.texts, ["a" * 8, "b" * 8 + NL + "c"])

    def test_needs_no_key_and_no_discovery(self):
        http = FakeGoogle()
        self.translator(http).translate(["a"])
        self.assertEqual(http.keys, [None])
        self.assertEqual(len(http.texts), 1)

    def test_writes_what_it_learned_to_the_cache(self):
        self.translator(FakeGoogle({"a": "가"})).translate(["a"])
        self.assertEqual(load_cache(self.cache), {"a": "가"})

    def test_a_cached_subject_costs_no_request(self):
        save_cache(self.cache, {"a": "가"})
        http = FakeGoogle()
        self.assertEqual(self.translator(http).translate(["a"]), {"a": "가"})
        self.assertEqual(http.texts, [])

    def test_an_answer_identical_to_the_original_is_not_stored(self):
        http = FakeGoogle({"ComfyUI": "ComfyUI"})
        self.assertEqual(self.translator(http).translate(["ComfyUI"]), {})
        self.assertEqual(load_cache(self.cache), {})

    def test_a_blocked_endpoint_means_no_translation_and_no_crash(self):
        translator = self.translator(FakeGoogle(blocked=True))
        self.assertEqual(translator.translate(["a"]), {})
        self.assertTrue(translator.unreachable)
        self.assertFalse(translator.needs_key)

    def test_knows_what_it_is(self):
        translator = self.translator(FakeGoogle())
        self.assertEqual(translator.name, "google")
        self.assertEqual(translator.used_model, translate.GOOGLE_LABEL)

    def test_counts_what_it_translated(self):
        translator = self.translator(FakeGoogle())
        translator.translate(["a", "b"])
        self.assertEqual(translator.translated, 2)

    def test_stops_when_the_time_budget_runs_out(self):
        ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])
        http = FakeGoogle()
        translator = self.translator(http, batch=1, budget=10.0,
                                     clock=lambda: next(ticks))
        translator.translate(["a", "b", "c"])
        self.assertEqual(http.texts, ["a"])

    def test_works_without_a_cache_file(self):
        translator = GoogleTranslator(cache_path=None, http=FakeGoogle({"a": "가"}))
        self.assertEqual(translator.translate(["a"]), {"a": "가"})

    def test_uses_the_real_endpoint_by_default(self):
        self.assertIs(GoogleTranslator().http, translate.http_json)


class ApplyToDiff(unittest.TestCase):
    def diff(self, core=None, nodes=None):
        return {"core": core, "nodes_changed": nodes or []}

    def test_attaches_korean_to_a_core_commit(self):
        d = self.diff(core={"commits": [{"subject": "a"}]})
        apply_to_diff(d, LLMTranslator(endpoints=["http://a/v1"],
                                    http=FakeHttp({"a": "가"})))
        self.assertEqual(d["core"]["commits"][0]["subject_ko"], "가")

    def test_attaches_korean_to_a_node_commit(self):
        d = self.diff(nodes=[{"commits": [{"subject": "a"}]}])
        apply_to_diff(d, LLMTranslator(endpoints=["http://a/v1"],
                                    http=FakeHttp({"a": "가"})))
        self.assertEqual(d["nodes_changed"][0]["commits"][0]["subject_ko"], "가")

    def test_leaves_an_untranslated_commit_alone(self):
        d = self.diff(core={"commits": [{"subject": "a"}]})
        apply_to_diff(d, LLMTranslator(endpoints=["http://a/v1"],
                                    http=FakeHttp(fail_on=["http"])))
        self.assertNotIn("subject_ko", d["core"]["commits"][0])

    def test_a_diff_without_commits_asks_nothing(self):
        http = FakeHttp()
        d = self.diff(nodes=[{"name": "n"}])
        self.assertEqual(apply_to_diff(d, LLMTranslator(http=http)), 0)
        self.assertEqual(http.calls, [])

    def test_returns_the_number_translated(self):
        d = self.diff(core={"commits": [{"subject": "a"}, {"subject": "b"}]})
        count = apply_to_diff(d, LLMTranslator(endpoints=["http://a/v1"], http=FakeHttp()))
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
