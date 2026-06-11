import json

import pytest
from fastapi import HTTPException

from template_store import ConfigTemplate, TemplateStore


def test_template_store_persists_user_templates_without_secrets(tmp_path):
    template_path = tmp_path / "templates.json"
    store = TemplateStore(store_path=str(template_path))

    template = store.create(
        ConfigTemplate(
            name="Secure Template",
            description="No secrets on disk",
            tags=["security"],
            saveOptions={"includeAPIKeys": True},
            config={
                "llm": {"apiKey": "global-secret", "baseUrl": "https://example.test/v1"},
                "players": [
                    {
                        "id": 1,
                        "name": "P1",
                        "apiKey": "player-secret",
                        "backend_config": {
                            "extra_env": {
                                "CUSTOM_FLAG": "enabled",
                                "SECRET_TOKEN": "token-secret",
                            }
                        },
                    }
                ],
            },
        )
    )

    persisted = json.loads(template_path.read_text(encoding="utf-8"))
    stored_config = persisted[0]["config"]
    assert "apiKey" not in stored_config["llm"]
    assert "apiKey" not in stored_config["players"][0]
    assert "SECRET_TOKEN" not in stored_config["players"][0]["backend_config"]["extra_env"]
    assert stored_config["players"][0]["backend_config"]["extra_env"]["CUSTOM_FLAG"] == "enabled"
    assert template["config"] == stored_config


def test_template_store_can_strip_player_names_when_requested(tmp_path):
    store = TemplateStore(store_path=str(tmp_path / "templates.json"))

    template = store.create(
        ConfigTemplate(
            name="Anonymous",
            saveOptions={"includePlayerNames": False},
            config={
                "match": {"duration": 1200},
                "players": [{"id": 1, "name": "Alice", "model": "gpt-5.5"}],
            },
        )
    )

    assert template["playerCount"] == 1
    assert template["duration"] == 20
    assert template["config"]["players"][0] == {"id": 1, "model": "gpt-5.5"}


def test_template_store_scrubs_legacy_template_file_on_load(tmp_path):
    template_path = tmp_path / "templates.json"
    template_path.write_text(
        json.dumps(
            [
                {
                    "id": "tpl-legacy",
                    "name": "Legacy",
                    "description": "Old template with secrets",
                    "tags": [],
                    "isSystem": False,
                    "usageCount": 0,
                    "playerCount": 1,
                    "duration": 20,
                    "createdAt": "2026-01-01T00:00:00",
                    "lastUsedAt": None,
                    "config": {
                        "llm": {"apiKey": "legacy-global-secret"},
                        "players": [{"id": 1, "token": "legacy-player-token"}],
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = TemplateStore(store_path=str(template_path))
    template = store.get("tpl-legacy")
    persisted = json.loads(template_path.read_text(encoding="utf-8"))
    serialized = json.dumps({"api": template, "disk": persisted}, ensure_ascii=False)

    assert "legacy-global-secret" not in serialized
    assert "legacy-player-token" not in serialized
    assert "apiKey" not in template["config"]["llm"]
    assert "token" not in template["config"]["players"][0]


def test_template_store_recovers_empty_template_file(tmp_path):
    template_path = tmp_path / "templates.json"
    template_path.write_text("", encoding="utf-8")

    store = TemplateStore(store_path=str(template_path))

    assert store.list()
    assert json.loads(template_path.read_text(encoding="utf-8")) == []


def test_template_store_rejects_system_template_mutations(tmp_path):
    store = TemplateStore(store_path=str(tmp_path / "templates.json"))

    with pytest.raises(HTTPException) as update_error:
        store.update(
            "sys-2player-default",
            ConfigTemplate(name="Nope", config={"players": []}),
        )
    assert update_error.value.status_code == 404

    with pytest.raises(HTTPException) as delete_error:
        store.delete("sys-2player-default")
    assert delete_error.value.status_code == 403
