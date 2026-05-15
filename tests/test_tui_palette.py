"""Regression tests for the palette entry transforms.

Backend Tag/Folder/Recording IDs are UUID strings; a prior version cast
them to int and crashed. These tests pin the pure transforms so the
crash can't return, and cover the LLM-model normalisation that accepts
multiple response shapes.
"""
from __future__ import annotations

from tui.palette import (
    entries_from_folders,
    entries_from_models,
    entries_from_tags,
)


def test_entries_from_folders_uuid_ids():
    out = entries_from_folders([
        {"id": "2e9c6cc2-e08c-459e-917c-0d0a4d634322", "name": "ideas"},
        {"id": "abcd-efgh", "name": "work"},
    ])
    assert len(out) == 2
    assert out[0].key == "folder:2e9c6cc2-e08c-459e-917c-0d0a4d634322"
    assert out[0].display.endswith("ideas")


def test_entries_from_tags_uuid_ids():
    out = entries_from_tags([
        {"id": "u-1234", "name": "meeting"},
        {"id": "u-5678", "name": "podcast"},
    ])
    assert {e.display for e in out} == {"# meeting", "# podcast"}


def test_entries_from_tags_skips_missing_id():
    out = entries_from_tags([{"name": "no-id"}, {"id": "x", "name": "ok"}])
    assert len(out) == 1
    assert out[0].display == "# ok"


def test_entries_from_models_mixed_shapes():
    out = entries_from_models({"models": [
        {"name": "llama3.1"},
        {"model": "qwen2.5"},
        "mistral",
    ]})
    names = {e.key.split(":", 1)[1] for e in out}
    assert names == {"llama3.1", "qwen2.5", "mistral"}


def test_entries_from_models_handles_bare_list():
    out = entries_from_models(["llama3.1", "qwen2.5"])
    assert {e.display for e in out} == {"⚡ llama3.1", "⚡ qwen2.5"}


def test_entries_from_models_empty():
    assert entries_from_models({}) == []
    assert entries_from_models(None) == []
