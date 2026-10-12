"""An optional legacy registry cannot make explicit project collection fail."""
import json

import collect


def test_missing_legacy_installation_returns_no_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "ilk_data_root", lambda: tmp_path)

    def unavailable():
        raise FileNotFoundError("no installed skills")

    monkeypatch.setattr(collect, "_skill_root", unavailable)
    assert collect.read_projects_registry() == []


def test_canonical_registry_precedes_unavailable_legacy_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "ilk_data_root", lambda: tmp_path)
    (tmp_path / "projects.json").write_text(json.dumps({"projects": [{"name": "example"}]}))

    def unexpected():
        raise AssertionError("canonical registry must not probe legacy installation")

    monkeypatch.setattr(collect, "_skill_root", unexpected)
    assert collect.read_projects_registry() == [{"name": "example"}]
