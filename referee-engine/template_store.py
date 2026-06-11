"""Configuration template persistence for the referee API."""

import copy
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from redaction import is_sensitive_key


logger = logging.getLogger(__name__)


class ConfigTemplate(BaseModel):
    """User-supplied match configuration template."""

    name: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = []
    config: dict
    saveOptions: Optional[dict] = None


class TemplateStore:
    """In-memory template store persisted to templates.json."""

    SYSTEM_TEMPLATES = [
        {
            "id": "sys-2player-default",
            "name": "2 Player Quick Match",
            "description": "Two players, quick test match with defense and attack phases.",
            "tags": ["quick", "2-player", "default"],
            "isSystem": True,
            "usageCount": 0,
            "playerCount": 2,
            "duration": 20,
            "createdAt": "2026-01-01T00:00:00Z",
            "lastUsedAt": None,
            "config": {
                "match": {"name": "2 Player Quick Match", "duration": 1200, "phases": {"defense": 600, "attack": 600}},
                "llm": {"provider": "custom", "baseUrl": "https://api.findmini.top/gpt"},
                "players": [
                    {"id": 1, "model": "gpt-5.5", "gatewayPort": 18789},
                    {"id": 2, "model": "gpt-5.5", "gatewayPort": 18790},
                ],
                "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
                "flags": {"refreshInterval": 180},
            },
        },
        {
            "id": "sys-4player-default",
            "name": "4 Player Standard Match",
            "description": "Four players using the default model with standard scoring.",
            "tags": ["standard", "4-player", "default"],
            "isSystem": True,
            "usageCount": 0,
            "playerCount": 4,
            "duration": 40,
            "createdAt": "2026-01-01T00:00:00Z",
            "lastUsedAt": None,
            "config": {
                "match": {"name": "4 Player Standard Match", "duration": 2400, "phases": {"defense": 600, "attack": 1800}},
                "llm": {"provider": "custom", "baseUrl": "https://api.findmini.top/gpt"},
                "players": [
                    {"id": 1, "model": "gpt-5.5", "gatewayPort": 18789},
                    {"id": 2, "model": "gpt-5.5", "gatewayPort": 18790},
                    {"id": 3, "model": "gpt-5.5", "gatewayPort": 18791},
                    {"id": 4, "model": "gpt-5.5", "gatewayPort": 18792},
                ],
                "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
                "flags": {"refreshInterval": 300},
            },
        },
        {
            "id": "sys-4player-mixed",
            "name": "4 Player Mixed Match",
            "description": "Compare attack and defense behavior across several models.",
            "tags": ["mixed", "4-player"],
            "isSystem": True,
            "usageCount": 0,
            "playerCount": 4,
            "duration": 40,
            "createdAt": "2026-01-01T00:00:00Z",
            "lastUsedAt": None,
            "config": {
                "match": {"name": "4 Player Mixed Match", "duration": 2400, "phases": {"defense": 600, "attack": 1800}},
                "llm": {"provider": "custom", "baseUrl": "https://api.findmini.top/gpt"},
                "players": [
                    {"id": 1, "model": "gpt-5.5", "gatewayPort": 18789},
                    {"id": 2, "model": "gpt-5.5", "gatewayPort": 18790},
                    {"id": 3, "model": "gpt-5.5", "gatewayPort": 18791},
                    {"id": 4, "model": "gpt-5.5", "gatewayPort": 18792},
                ],
                "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
                "flags": {"refreshInterval": 300},
            },
        },
        {
            "id": "sys-8player-brawl",
            "name": "8 Player Brawl",
            "description": "Eight players in a longer mixed match.",
            "tags": ["large", "8-player"],
            "isSystem": True,
            "usageCount": 0,
            "playerCount": 8,
            "duration": 120,
            "createdAt": "2026-01-01T00:00:00Z",
            "lastUsedAt": None,
            "config": {
                "match": {"name": "8 Player Brawl", "duration": 7200, "phases": {"defense": 600, "attack": 6600}},
                "llm": {"provider": "custom", "baseUrl": "https://api.findmini.top/gpt"},
                "players": [
                    {"id": i, "model": "gpt-5.5", "gatewayPort": 18788 + i}
                    for i in range(1, 9)
                ],
                "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
                "flags": {"refreshInterval": 300},
            },
        },
    ]

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = store_path or os.getenv(
            "OPENCLAW_TEMPLATES_PATH",
            os.path.join(os.path.dirname(__file__), "templates.json"),
        )
        self._templates: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        for tpl in self.SYSTEM_TEMPLATES:
            self._templates[tpl["id"]] = copy.deepcopy(tpl)

        if os.path.exists(self.store_path):
            try:
                with open(self.store_path, encoding="utf-8") as f:
                    raw = f.read()
                if not raw.strip():
                    self._save()
                    return
                user_templates = json.loads(raw)
                if not isinstance(user_templates, list):
                    raise ValueError("templates.json must contain a JSON array")
                for tpl in user_templates:
                    if not isinstance(tpl, dict) or tpl.get("isSystem"):
                        continue
                    if isinstance(tpl.get("config"), dict):
                        tpl["config"] = self._strip_sensitive_config(tpl["config"])
                    if isinstance(tpl.get("id"), str):
                        self._templates[tpl["id"]] = tpl
                self._save()
            except Exception as e:
                logger.warning(f"Failed to load templates.json: {e}")

    def _save(self) -> None:
        try:
            user_templates = [
                tpl for tpl in self._templates.values()
                if not tpl.get("isSystem")
            ]
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            with open(self.store_path, "w", encoding="utf-8") as f:
                json.dump(user_templates, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save templates.json: {e}")

    def _filter_config_for_storage(self, data: ConfigTemplate) -> dict:
        opts = data.saveOptions or {}
        config = copy.deepcopy(data.config)
        config = self._strip_sensitive_config(config)

        if not opts.get("includePlayerNames", True) and isinstance(config.get("players"), list):
            config["players"] = [
                {key: value for key, value in player.items() if key != "name"}
                if isinstance(player, dict) else player
                for player in config["players"]
            ]
        return config

    @classmethod
    def _strip_sensitive_config(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._strip_sensitive_config(item)
                for key, item in value.items()
                if not is_sensitive_key(key)
            }
        if isinstance(value, list):
            return [cls._strip_sensitive_config(item) for item in value]
        return value

    @staticmethod
    def _template_duration_minutes(config: dict) -> int:
        duration = (config.get("match") or {}).get("duration", 0) if isinstance(config, dict) else 0
        try:
            return int(duration) // 60
        except (TypeError, ValueError):
            return 0

    def list(self) -> List[dict]:
        return list(self._templates.values())

    def get(self, template_id: str) -> Optional[dict]:
        return self._templates.get(template_id)

    def create(self, data: ConfigTemplate) -> dict:
        template_id = f"tpl-{uuid.uuid4().hex[:8]}"
        config = self._filter_config_for_storage(data)
        player_count = len(config.get("players", []))
        duration_min = self._template_duration_minutes(config)

        tpl = {
            "id": template_id,
            "name": data.name,
            "description": data.description,
            "tags": data.tags or [],
            "isSystem": False,
            "usageCount": 0,
            "playerCount": player_count,
            "duration": duration_min,
            "createdAt": datetime.now().isoformat(),
            "lastUsedAt": None,
            "config": config,
        }
        self._templates[template_id] = tpl
        self._save()
        return tpl

    def update(self, template_id: str, data: ConfigTemplate) -> dict:
        tpl = self._templates.get(template_id)
        if not tpl or tpl.get("isSystem"):
            raise HTTPException(status_code=404, detail="Template not found or is a system template")
        config = self._filter_config_for_storage(data)
        tpl.update({
            "name": data.name,
            "description": data.description,
            "tags": data.tags or [],
            "config": config,
            "playerCount": len(config.get("players", [])),
            "duration": self._template_duration_minutes(config),
        })
        self._save()
        return tpl

    def delete(self, template_id: str) -> None:
        tpl = self._templates.get(template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found")
        if tpl.get("isSystem"):
            raise HTTPException(status_code=403, detail="Cannot delete system template")
        del self._templates[template_id]
        self._save()

    def increment_usage(self, template_id: str) -> None:
        tpl = self._templates.get(template_id)
        if tpl:
            tpl["usageCount"] = tpl.get("usageCount", 0) + 1
            tpl["lastUsedAt"] = datetime.now().isoformat()
            if not tpl.get("isSystem"):
                self._save()
