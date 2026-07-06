"""Gateway tests (llm.py) — failures must be loud-but-non-fatal, and cost must be
priced from the model id, never hand-copied constants."""
import logging

from advanced_reporting import llm


def test_call_without_key_returns_none_with_reason(monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_env_loaded", True)   # don't pick up a real project .env
    with caplog.at_level(logging.WARNING, logger="advanced_reporting.llm"):
        data, info = llm.call("hello", model="claude-haiku-4-5")
    assert data is None
    assert "ANTHROPIC_API_KEY" in info["error"]
    assert any("skipped" in r.message for r in caplog.records)   # loud, not silent


def test_llm_enabled_reflects_environment(monkeypatch):
    monkeypatch.setattr(llm, "_env_loaded", True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.llm_enabled() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.llm_enabled() is True


def test_cost_priced_by_model_prefix():
    assert llm.cost_usd("claude-sonnet-5", 1_000_000, 0) == 3.0
    assert llm.cost_usd("claude-haiku-4-5-20251001", 0, 1_000_000) == 5.0  # dated id matches
    assert llm.cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 30.0


def test_unknown_model_costs_none_not_wrong(caplog):
    with caplog.at_level(logging.WARNING, logger="advanced_reporting.llm"):
        assert llm.cost_usd("some-future-model", 1_000_000, 0) is None
    assert any("no pricing entry" in r.message for r in caplog.records)


def test_env_file_is_honored(monkeypatch, tmp_path):
    # the documented setup — a key in the project .env — used to do nothing because
    # nothing on the LLM paths ever loaded the file
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    from advanced_reporting.utils import load_env_file
    load_env_file(env)
    assert llm.llm_enabled() is True
