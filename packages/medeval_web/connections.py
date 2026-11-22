"""Connections: bring your own provider, model and key.

A *connection* describes one endpoint the owner wants to use as a judge or as a
screening model. Connection **metadata** (name, base URL, model, caps) is stored
on disk as JSON; the **key is never stored** — it lives only in this process's
:class:`KeyStore`, is sent from the browser over loopback in a request body, and
is forgotten when the app stops or when the owner asks.

Preset base URLs were read from each provider's official documentation at build
time (see ``VERIFY_ME.md`` for the sources and date); every preset field stays
editable, so a wrong or moved URL is a user edit, not a code change.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = [
    "PRESETS",
    "Connection",
    "ConnectionStore",
    "KeyStore",
    "RoleAssignments",
    "base_url_problems",
    "base_url_warnings",
    "sanitize_key_tail",
]

#: Provider presets: convenience only. A preset fills a base URL and a docs link;
#: every field stays editable and **no preset carries a price**. Prices start
#: empty on every connection because a stale hard-coded price is worse than no
#: price: the cost estimate then says "unavailable, call cap is binding" and the
#: call cap is enforced instead. ``pricing_url`` is where the owner copies the
#: current price from, and ``last_verified`` is when the base URL was last read
#: from that documentation and manually corrected. Sources (read 2026-11-18):
#:   - DeepSeek:   https://api-docs.deepseek.com/quick_start/your_first_api_call
#:                 pricing: https://api-docs.deepseek.com/quick_start/pricing
#:   - OpenRouter: https://openrouter.ai/docs/api_reference/versioning.md
#:                 pricing: https://openrouter.ai/models
#:   - OpenAI:     https://developers.openai.com/api/reference/overview
#:                 pricing: https://platform.openai.com/docs/pricing
#:   - Ollama:     https://docs.ollama.com/api/openai-compatibility.md (local, no price)
PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_style": "chat",
        "requires_key": True,
        "docs_url": "https://api-docs.deepseek.com/quick_start/your_first_api_call",
        "pricing_url": "https://api-docs.deepseek.com/quick_start/pricing",
        "last_verified": "2026-11-18",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_style": "chat",
        "requires_key": True,
        "docs_url": "https://openrouter.ai/docs/api_reference/versioning.md",
        "pricing_url": "https://openrouter.ai/models",
        "last_verified": "2026-11-18",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_style": "chat",
        "requires_key": True,
        "docs_url": "https://developers.openai.com/api/reference/overview",
        "pricing_url": "https://platform.openai.com/docs/pricing",
        "last_verified": "2026-11-18",
    },
    "ollama": {
        "label": "Ollama (local, no key)",
        "base_url": "http://localhost:11434/v1",
        "api_style": "chat",
        "requires_key": False,
        "docs_url": "https://docs.ollama.com/api/openai-compatibility.md",
        "pricing_url": "",
        "last_verified": "2026-11-18",
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "base_url": "",
        "api_style": "chat",
        "requires_key": False,
        "docs_url": "",
        "pricing_url": "",
        "last_verified": "",
    },
}

#: Hosts that cannot leave the machine; plain http to them is not a leak risk.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def base_url_problems(base_url: str) -> list[str]:
    """``base_url`` rules: http(s) only, and no credentials embedded in the URL.

    ``https://user:pass@host/v1`` would put a secret in every log line, manifest
    and screenshot that prints the URL, so it is refused rather than sanitised.
    """
    from urllib.parse import urlsplit

    url = (base_url or "").strip()
    if not url:
        return ["base_url is required"]
    parts = urlsplit(url)
    problems: list[str] = []
    if parts.scheme not in {"http", "https"}:
        problems.append("base_url must start with http:// or https:// (no other scheme is allowed)")
    if parts.username or parts.password:
        problems.append(
            "base_url must not embed credentials (user:pass@host); the key goes in the API-key field "
            "and is kept in memory only"
        )
    if not parts.hostname:
        problems.append("base_url must name a host")
    return problems


def base_url_warnings(base_url: str) -> list[str]:
    """Non-blocking warnings: plain ``http`` to a host that is not loopback."""
    from urllib.parse import urlsplit

    parts = urlsplit((base_url or "").strip())
    if parts.scheme == "http" and (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        return [
            f"{parts.hostname or 'this host'} is not loopback and the URL uses plain http: the API key and "
            "the prompt text would travel unencrypted. Use https if the provider supports it."
        ]
    return []

VALID_API_STYLES = ("chat", "responses")


def sanitize_key_tail(key: str | None) -> str | None:
    """Last 4 characters of a key, for the 'key set ...1234' display."""
    if not key:
        return None
    return key[-4:]


@dataclass(frozen=True)
class Connection:
    """One provider endpoint. The API key is never part of this object."""

    id: str
    name: str
    provider_preset: str
    base_url: str
    api_style: str = "chat"
    model: str = ""
    family_label: str = ""
    max_tokens: int = 2048
    temperature: float = 0.3
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    per_connection_call_cap: int = 500
    per_connection_usd_cap: float = 5.0
    created_utc: str = ""
    updated_utc: str = ""

    def as_dict(self, *, key_tail: str | None = None) -> dict[str, Any]:
        """Serializable form. A key is reduced to its last 4 characters."""
        return {
            "id": self.id,
            "name": self.name,
            "provider_preset": self.provider_preset,
            "base_url": self.base_url,
            "api_style": self.api_style,
            "model": self.model,
            "family_label": self.family_label,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "price_in_per_mtok": self.price_in_per_mtok,
            "price_out_per_mtok": self.price_out_per_mtok,
            "per_connection_call_cap": self.per_connection_call_cap,
            "per_connection_usd_cap": self.per_connection_usd_cap,
            "created_utc": self.created_utc,
            "updated_utc": self.updated_utc,
            "key_set": key_tail is not None,
            "key_last4": key_tail,
        }

    def validate(self) -> list[str]:
        """Field-level problems that must be fixed before the connection is used."""
        problems: list[str] = []
        if not self.name.strip():
            problems.append("name is required")
        if self.provider_preset not in PRESETS:
            problems.append(f"provider_preset must be one of {sorted(PRESETS)}")
        problems.extend(base_url_problems(self.base_url))
        if self.api_style not in VALID_API_STYLES:
            problems.append(f"api_style must be one of {list(VALID_API_STYLES)}")
        if self.max_tokens < 16:
            problems.append("max_tokens must be at least 16 (reasoning models need room)")
        if not 0 <= self.temperature <= 2:
            problems.append("temperature must be between 0 and 2")
        if self.per_connection_call_cap < 1:
            problems.append("per_connection_call_cap must be at least 1")
        if self.per_connection_usd_cap < 0:
            problems.append("per_connection_usd_cap must not be negative")
        if self.price_in_per_mtok is not None and self.price_in_per_mtok < 0:
            problems.append("price_in_per_mtok must not be negative")
        if self.price_out_per_mtok is not None and self.price_out_per_mtok < 0:
            problems.append("price_out_per_mtok must not be negative")
        return problems

    def warnings(self) -> list[str]:
        """Non-blocking advice: shown in the app, never used to refuse a save."""
        return base_url_warnings(self.base_url)


class KeyStore:
    """API keys held only in process memory, keyed by connection id.

    Nothing here touches disk, and ``repr``/``str`` never reveal a key: only
    ``key_tail`` does, and it shows the last 4 characters by design.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[str, str] = {}

    def set(self, connection_id: str, key: str) -> None:
        key = key.strip()
        if not key:
            raise ValueError("key must not be empty")
        with self._lock:
            self._keys[connection_id] = key

    def get(self, connection_id: str) -> str | None:
        with self._lock:
            return self._keys.get(connection_id)

    def tail(self, connection_id: str) -> str | None:
        return sanitize_key_tail(self.get(connection_id))

    def forget(self, connection_id: str) -> bool:
        with self._lock:
            return self._keys.pop(connection_id, None) is not None

    def forget_all(self) -> int:
        with self._lock:
            count = len(self._keys)
            self._keys.clear()
            return count

    def all_keys(self) -> tuple[str, ...]:
        """Every stored key, for redaction sinks. Never log the result."""
        with self._lock:
            return tuple(self._keys.values())


@dataclass
class RoleAssignments:
    """Which connection plays which role.

    Judges are ``judge_1``, ``judge_2``, ... (any number); the screening model is
    a separate optional role that must never also be a judge.
    """

    judge_1: str | None = None
    judge_2: str | None = None
    screening: str | None = None
    extra_judges: list[str] = field(default_factory=list)

    def judge_ids(self) -> list[tuple[str, str]]:
        """Ordered (role, connection_id) pairs for every judge role."""
        roles: list[tuple[str, str]] = []
        if self.judge_1:
            roles.append(("judge_1", self.judge_1))
        if self.judge_2:
            roles.append(("judge_2", self.judge_2))
        for index, connection_id in enumerate(self.extra_judges, start=3):
            if connection_id:
                roles.append((f"judge_{index}", connection_id))
        return roles

    def as_dict(self) -> dict[str, Any]:
        return {
            "judges": [{"role": role, "connection_id": connection_id} for role, connection_id in self.judge_ids()],
            "screening": self.screening,
        }

    def validate(self) -> list[str]:
        problems: list[str] = []
        seen: set[str] = set()
        judges = self.judge_ids()
        if len(judges) < 1:
            problems.append("at least one judge role must be assigned")
        for _role, connection_id in judges:
            if connection_id in seen:
                problems.append("the same connection cannot hold two judge roles")
            seen.add(connection_id)
        if self.screening:
            if self.screening in seen:
                problems.append(
                    "the screening model must not also be a judge; cross-family error correlation "
                    "and screening independence both depend on them being different connections"
                )
            if not self.judge_ids():
                problems.append("a screening model without any judge has nothing to screen for")
        if self.judge_2 and not self.judge_1:
            problems.append("judge_2 cannot be assigned without judge_1")
        return problems


class ConnectionStore:
    """Connections and role assignments, persisted without keys."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._connections: dict[str, Connection] = {}
        self._roles = RoleAssignments()
        self._load()

    # -- persistence ------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in payload.get("connections") or []:
            row = dict(row)
            row.pop("key", None)          # a key must never be read back from disk
            row.pop("key_set", None)
            row.pop("key_last4", None)
            try:
                connection = Connection(**row)
            except TypeError:
                continue
            self._connections[connection.id] = connection
        roles = payload.get("roles") or {}
        self._roles = RoleAssignments(
            judge_1=roles.get("judge_1"),
            judge_2=roles.get("judge_2"),
            screening=roles.get("screening"),
            extra_judges=list(roles.get("extra_judges") or []),
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "connections": [self._connection_row(connection) for connection in self._connections.values()],
            "roles": {
                "judge_1": self._roles.judge_1,
                "judge_2": self._roles.judge_2,
                "screening": self._roles.screening,
                "extra_judges": self._roles.extra_judges,
            },
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _connection_row(connection: Connection) -> dict[str, Any]:
        return {k: v for k, v in connection.as_dict().items() if k not in {"key_set", "key_last4"}}

    # -- connections ------------------------------------------------------ #
    def list(self) -> list[Connection]:
        with self._lock:
            return sorted(self._connections.values(), key=lambda row: row.created_utc)

    def get(self, connection_id: str) -> Connection | None:
        with self._lock:
            return self._connections.get(connection_id)

    def _name_conflict(self, name: str, *, ignore_id: str | None = None) -> str | None:
        """A connection name must be unique; it is how the owner tells judges apart."""
        wanted = name.strip().casefold()
        if not wanted:
            return None
        for existing in self._connections.values():
            if existing.id == ignore_id:
                continue
            if existing.name.strip().casefold() == wanted:
                return f"a connection named {existing.name!r} already exists; names must be unique"
        return None

    def create(self, fields: dict[str, Any]) -> tuple[Connection, list[str]]:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        preset = PRESETS.get(str(fields.get("provider_preset", "")), {})
        connection = Connection(
            id=str(fields.get("id") or f"conn-{uuid.uuid4().hex[:10]}"),
            name=str(fields.get("name") or "").strip(),
            provider_preset=str(fields.get("provider_preset") or "custom"),
            base_url=str(fields.get("base_url") or preset.get("base_url", "")).strip(),
            api_style=str(fields.get("api_style") or preset.get("api_style", "chat")).strip(),
            model=str(fields.get("model") or "").strip(),
            family_label=str(fields.get("family_label") or "").strip(),
            max_tokens=int(fields.get("max_tokens") or 2048),
            temperature=float(fields.get("temperature") or 0.3),
            price_in_per_mtok=_optional_float(fields.get("price_in_per_mtok")),
            price_out_per_mtok=_optional_float(fields.get("price_out_per_mtok")),
            per_connection_call_cap=int(fields.get("per_connection_call_cap") or 500),
            per_connection_usd_cap=float(fields.get("per_connection_usd_cap") or 5.0),
            created_utc=now,
            updated_utc=now,
        )
        problems = connection.validate()
        if problems:
            return connection, problems
        with self._lock:
            conflict = self._name_conflict(connection.name)
            if conflict:
                return connection, [conflict]
            self._connections[connection.id] = connection
            self._save()
        return connection, []

    def update(self, connection_id: str, fields: dict[str, Any]) -> tuple[Connection | None, list[str]]:
        with self._lock:
            current = self._connections.get(connection_id)
            if current is None:
                return None, ["unknown connection"]
            merged: dict[str, Any] = {**current.as_dict(), **{k: v for k, v in fields.items() if k != "id"}}
            merged["id"] = connection_id
            merged["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            candidate_fields = {
                "name": merged.get("name"),
                "provider_preset": merged.get("provider_preset"),
                "base_url": merged.get("base_url"),
                "api_style": merged.get("api_style"),
                "model": merged.get("model"),
                "family_label": merged.get("family_label"),
                "max_tokens": merged.get("max_tokens"),
                "temperature": merged.get("temperature"),
                "price_in_per_mtok": merged.get("price_in_per_mtok"),
                "price_out_per_mtok": merged.get("price_out_per_mtok"),
                "per_connection_call_cap": merged.get("per_connection_call_cap"),
                "per_connection_usd_cap": merged.get("per_connection_usd_cap"),
                "created_utc": merged.get("created_utc"),
                "updated_utc": merged.get("updated_utc"),
            }
            updated = replace(current, **candidate_fields)
            problems = updated.validate()
            if problems:
                return current, problems
            conflict = self._name_conflict(updated.name, ignore_id=connection_id)
            if conflict:
                return current, [conflict]
            self._connections[connection_id] = updated
            self._save()
            return updated, []

    def delete(self, connection_id: str) -> bool:
        with self._lock:
            if connection_id not in self._connections:
                return False
            del self._connections[connection_id]
            roles = self._roles
            if roles.judge_1 == connection_id:
                roles.judge_1 = None
            if roles.judge_2 == connection_id:
                roles.judge_2 = None
            if roles.screening == connection_id:
                roles.screening = None
            roles.extra_judges = [row for row in roles.extra_judges if row != connection_id]
            self._save()
            return True

    # -- roles ------------------------------------------------------------ #
    def roles(self) -> RoleAssignments:
        with self._lock:
            return self._roles

    def set_roles(self, payload: dict[str, Any]) -> tuple[RoleAssignments, list[str]]:
        with self._lock:
            judges = payload.get("judges") or []
            extra: list[str] = []
            for row in judges:
                role = str(row.get("role", ""))
                if role.startswith("judge_"):
                    suffix = role.split("_", 1)[1]
                    if not suffix.isdigit() or int(suffix) < 3:
                        continue
                    extra.append(row.get("connection_id") or "")
            roles = RoleAssignments(
                judge_1=_role_value(judges, "judge_1"),
                judge_2=_role_value(judges, "judge_2"),
                screening=payload.get("screening") or None,
                extra_judges=extra,
            )
            for role, connection_id in roles.judge_ids():
                if connection_id not in self._connections:
                    return self._roles, [f"{role}: unknown connection {connection_id!r}"]
            if roles.screening and roles.screening not in self._connections:
                return self._roles, [f"screening: unknown connection {roles.screening!r}"]
            problems = roles.validate()
            if problems:
                return self._roles, problems
            self._roles = roles
            self._save()
            return roles, []


def _role_value(judges: list[dict[str, Any]], role: str) -> str | None:
    for row in judges:
        if row.get("role") == role:
            return row.get("connection_id") or None
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
