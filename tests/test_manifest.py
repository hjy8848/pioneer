"""[INDEPENDENT] The run manifest records what reproduces a run, and never the key."""
import json

from pioneer.manifest import write_manifest


def test_manifest_records_code_config_and_endpoints_without_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "test-only-secret")
    calls = []
    monkeypatch.setattr("pioneer.manifest._get", lambda url, timeout=5: calls.append(url) or {"data": [{"id": "m"}]})
    entry = write_manifest(tmp_path, "configs/retail_qwen36_local.yaml", argv=["evolve.py"],
                           api_base="http://a:1/v1,http://b:2/v1")
    assert entry["git"]["commit"] and entry["config"]["text"].startswith("#") and entry["config"]["sha256"]
    assert entry["world_model_sha256"] and entry["tau2_commit"]
    assert calls == ["http://a:1/v1/models", "http://a:1/version", "http://b:2/v1/models", "http://b:2/version"]
    write_manifest(tmp_path, "configs/retail_qwen36_local.yaml", argv=["evolve.py"], api_base="")
    text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert len(json.loads(text)) == 2 and "test-only-secret" not in text  # a resumed run appends
