from app.config import load_config, orchestrator_model


def test_config_is_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SECRETARY_CONFIG", raising=False)
    monkeypatch.delenv("SECRETARY_ORCHESTRATOR_MODEL", raising=False)
    assert load_config() == {}
    assert orchestrator_model({}) == "haiku"


def test_config_file_and_env_override(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "orchestrator:\n  model: sonnet\nmodules:\n  studies:\n    enabled: false\n", encoding="utf-8"
    )
    monkeypatch.setenv("SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SECRETARY_CONFIG", raising=False)
    monkeypatch.delenv("SECRETARY_ORCHESTRATOR_MODEL", raising=False)
    config = load_config()
    assert config["modules"]["studies"]["enabled"] is False
    assert orchestrator_model(config) == "sonnet"
    monkeypatch.setenv("SECRETARY_ORCHESTRATOR_MODEL", "opus")
    assert orchestrator_model(config) == "opus"
