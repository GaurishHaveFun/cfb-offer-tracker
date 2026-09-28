"""Finding config/schools.yaml however the package is installed."""
import pytest

from cfb_offers import config


def test_found_in_the_working_directory_like_github_actions(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "schools.yaml").write_text(
        "schools:\n  - name: Fake U\n    aliases: [Fake U]\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFB_SCHOOLS_CONFIG", raising=False)
    # simulate a site-packages install: no config next to the source
    monkeypatch.setattr(config, "DEFAULT_SCHOOLS_PATH", tmp_path / "missing" / "schools.yaml")
    assert [s.name for s in config.load_schools()] == ["Fake U"]


def test_env_override_wins(tmp_path, monkeypatch):
    f = tmp_path / "custom.yaml"
    f.write_text("schools:\n  - name: Other U\n")
    monkeypatch.setenv("CFB_SCHOOLS_CONFIG", str(f))
    assert [s.name for s in config.load_schools()] == ["Other U"]


def test_clear_error_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFB_SCHOOLS_CONFIG", raising=False)
    monkeypatch.setattr(config, "DEFAULT_SCHOOLS_PATH", tmp_path / "missing" / "schools.yaml")
    with pytest.raises(FileNotFoundError, match="CFB_SCHOOLS_CONFIG"):
        config.load_schools()
