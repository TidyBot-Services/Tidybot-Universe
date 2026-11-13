"""Task options must load on hosts without the Memory service's skill_tags module
(native sim worker; the Remote gateway on qifei with an older service package)."""
import importlib
import sys


def test_options_without_skill_tags(monkeypatch):
    monkeypatch.setitem(sys.modules, 'attention_memory_service.skill_tags', None)  # import -> ImportError
    tasks = importlib.import_module('benchmarks.attention_harness.dev_fixture_tasks')
    options = tasks.options()
    assert options and all('skill_tags' not in o and 'skill_tags_version' not in o for o in options)


def test_options_with_skill_tags():
    from attention_memory_service.skill_tags import VERSION
    tasks = importlib.import_module('benchmarks.attention_harness.dev_fixture_tasks')
    options = tasks.options()
    assert all(o['skill_tags_version'] == VERSION and isinstance(o['skill_tags'], list) for o in options)
