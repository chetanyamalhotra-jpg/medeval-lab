"""Headless bring-your-own-key connections: ``connections.yaml``.

A browser is convenient but a CI job or a server has none. This module is the
command-line equivalent of the app's Models page: it reads a small YAML file that
describes each provider endpoint and **names the environment variable** that
holds the key. The key itself is never in the file.

Minimal file (see ``connections.example.yaml``)::

    connections:
      - name: "judge A"
        base_url: "https://api.example.com/v1"
        model: "provider-model-a"
        key_env: "MY_PROVIDER_A_KEY"
        family_label: "family-a"
        role: judge
        repeats: 1
        call_cap: 200
        usd_cap: 2.0

Design rules, enforced by :func:`load_connections_file`:

* ``key``, ``api_key``, ``token`` and ``secret`` fields are rejected outright —
  a key belongs in the environment, never in a file that might be committed;
* ``base_url`` must be ``http`` or ``https`` and must not embed credentials
  (``user:pass@host``), because those would be written to logs and manifests;
* a non-loopback host needs ``key_env``; a loopback endpoint (a local model
  server) does not;
* prices are optional and have **no defaults**. When a connection declares no
  prices the cost estimate says so and the call cap is the binding limit.

Nothing here talks to a provider. The file is parsed, validated and turned into
the same judge bindings the web app builds, so both paths enforce identical caps.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "ConnectionsFile",
    "ConnectionsFileError",
    "FileConnection",
    "load_connections_file",
]

#: Fields a connection entry may contain. Anything else is refused rather than
#: ignored, so a typo cannot silently change the plan.
ALLOWED_FIELDS: tuple[str, ...] = (
    "name",
    "base_url",
    "model",
    "key_env",
    # v0.6 alias for key_env. Both mean "the environment variable that holds the
    # key"; the key itself is never accepted in the file under either name.
    "api_key_env",
    "family_label",
    "role",
    "repeats",
    "max_tokens",
    "temperature",
    "call_cap",
    "usd_cap",
    "price_in_per_mtok",
    "price_out_per_mtok",
    "api_style",
)

#: Field names that would mean a key is in the file. Their presence is an error,
#: not a warning: a committed key is a leak whether or not it is real.
KEY_FIELDS: tuple[str, ...] = ("key", "api_key", "apikey", "token", "secret", "password", "bearer")

ROLES: tuple[str, ...] = ("judge", "screening", "target")
API_STYLES: tuple[str, ...] = ("chat", "responses")

#: A host is treated as loopback when it cannot leave the machine.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


class ConnectionsFileError(ValueError):
    """The file is unusable. The message names the problem and never a key."""


@dataclass(frozen=True)
class FileConnection:
    """One endpoint from ``connections.yaml``, without its key."""

    name: str
    base_url: str
    model: str
    key_env: str = ""
    family_label: str = ""
    role: str = "judge"
    repeats: int = 1
    max_tokens: int = 2048
    temperature: float = 0.3
    call_cap: int = 200
    usd_cap: float = 2.0
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    api_style: str = "chat"

    # -- derived values --------------------------------------------------- #
    @property
    def judge_id(self) -> str:
        """Stable id used in run files and reports, derived from the name."""
        return _slug(self.name)

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()

    @property
    def is_loopback(self) -> bool:
        return self.host in _LOOPBACK_HOSTS

    @property
    def prices_declared(self) -> bool:
        return self.price_in_per_mtok is not None and self.price_out_per_mtok is not None

    def summary(self) -> dict[str, Any]:
        """Safe-to-print description. Contains the variable name, never its value."""
        return {
            "name": self.name,
            "role": self.role,
            "host": self.host,
            "model": self.model,
            "family_label": self.family_label or "family not set",
            "key_env": self.key_env or None,
            "repeats": self.repeats,
            "call_cap": self.call_cap,
            "usd_cap": self.usd_cap,
            "prices_declared": self.prices_declared,
        }

    def problems(self) -> list[str]:
        """Everything wrong with this entry, in plain words."""
        problems: list[str] = []
        if not self.name.strip():
            problems.append("name is required")
        if self.role not in ROLES:
            problems.append(f"role must be one of {list(ROLES)}")
        if self.api_style not in API_STYLES:
            problems.append(f"api_style must be one of {list(API_STYLES)}")
        if not self.model.strip():
            problems.append("model is required (the app never guesses a model id)")
        problems.extend(_base_url_problems(self.base_url))
        if not self.is_loopback and not self.key_env.strip():
            problems.append(f"key_env is required for a non-loopback host ({self.host or 'unknown host'})")
        if self.key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.key_env):
            problems.append(f"key_env {self.key_env!r} is not a valid environment variable name")
        if self.repeats < 1:
            problems.append("repeats must be at least 1")
        if self.max_tokens < 16:
            problems.append("max_tokens must be at least 16")
        if not 0 <= self.temperature <= 2:
            problems.append("temperature must be between 0 and 2")
        if self.call_cap < 1:
            problems.append("call_cap must be at least 1")
        if self.usd_cap < 0:
            problems.append("usd_cap must not be negative")
        for name, value in (("price_in_per_mtok", self.price_in_per_mtok), ("price_out_per_mtok", self.price_out_per_mtok)):
            if value is not None and value < 0:
                problems.append(f"{name} must not be negative")
        return problems

    def key(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Read the key from the environment. Never raises with a key in the text."""
        source = os.environ if environ is None else environ
        if not self.key_env:
            return None
        value = source.get(self.key_env)
        value = value.strip() if isinstance(value, str) else ""
        return value or None

    def as_binding(self, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
        """The binding dictionary the study runner consumes (same shape as the app)."""
        return {
            "judge_id": self.judge_id,
            "connection_id": f"file-{self.judge_id}"[:40],
            "family_label": self.family_label or "unlabelled-family",
            "backend": "api",
            "base_url": self.base_url,
            "api_style": self.api_style,
            "model": self.model,
            "api_key": self.key(environ),
            "temperature": self.temperature,
            "repeats": self.repeats,
            "max_tokens": self.max_tokens,
            "call_cap": self.call_cap,
            "usd_cap": self.usd_cap,
            "price_in_per_mtok": self.price_in_per_mtok,
            "price_out_per_mtok": self.price_out_per_mtok,
        }


@dataclass(frozen=True)
class ConnectionsFile:
    """A parsed ``connections.yaml``."""

    path: Path
    connections: tuple[FileConnection, ...] = field(default_factory=tuple)

    # -- views ------------------------------------------------------------ #
    @property
    def judges(self) -> tuple[FileConnection, ...]:
        return tuple(row for row in self.connections if row.role == "judge")

    @property
    def screeners(self) -> tuple[FileConnection, ...]:
        return tuple(row for row in self.connections if row.role == "screening")

    @property
    def targets(self) -> tuple[FileConnection, ...]:
        """Models under test for a v0.6 matrix run (never judges themselves)."""
        return tuple(row for row in self.connections if row.role == "target")

    def problems(self) -> list[str]:
        """File-level problems: caps, duplicates, role rules."""
        problems: list[str] = []
        for connection in self.connections:
            problems.extend(f"{connection.name or '<unnamed>'}: {problem}" for problem in connection.problems())
        if not self.judges and not self.targets:
            problems.append("at least one connection with role: judge or role: target is required")
        if len(self.screeners) > 1:
            problems.append("at most one connection may have role: screening")
        names = [row.name.strip().casefold() for row in self.connections]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            problems.append(f"duplicate connection names: {', '.join(duplicates)}")
        ids = [row.judge_id for row in self.connections]
        duplicate_ids = sorted({slug for slug in ids if ids.count(slug) > 1})
        if duplicate_ids:
            problems.append(f"connection names collide after normalising to ids: {', '.join(duplicate_ids)}")
        if self.screeners and self.judges:
            screener = self.screeners[0]
            for judge in self.judges:
                if screener.family_label and judge.family_label and screener.family_label.casefold() == judge.family_label.casefold():
                    problems.append(
                        f"the screening connection {screener.name!r} and judge {judge.name!r} declare the same "
                        "family_label; screening independence and cross-family error correlation both need them "
                        "to be different"
                    )
            if screener.name.casefold() in {judge.name.casefold() for judge in self.judges}:
                problems.append("the screening connection must not also be a judge")
        return problems

    def missing_keys(self, environ: Mapping[str, str] | None = None) -> list[str]:
        """Names of environment variables that are referenced but not set."""
        return [row.key_env for row in self.connections if row.key_env and not row.key(environ)]

    def judge_bindings(self, environ: Mapping[str, str] | None = None) -> tuple[dict[str, Any], ...]:
        return tuple(row.as_binding(environ) for row in self.judges)

    def target_bindings(self, environ: Mapping[str, str] | None = None) -> tuple[dict[str, Any], ...]:
        """Target-model bindings for ``medeval matrix run``."""
        rows = []
        for row in self.targets:
            binding = row.as_binding(environ)
            binding["role"] = "target"
            rows.append(binding)
        return tuple(rows)

    def screening_binding(self, environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
        if not self.screeners:
            return None
        binding = self.screeners[0].as_binding(environ)
        # The screening role has no judge id of its own in the study config.
        return {key: value for key, value in binding.items() if key != "judge_id"}

    def required_calls_for(self, *, n_items: int, repeats: int) -> int:
        """Upper bound on paid calls for a non-adaptive (Experiment 002) run."""
        return max(1, n_items) * max(1, repeats) * max(1, len(self.judges))

    def worst_case_usd(self, *, n_items: int, repeats: int, estimated_input_tokens: int = 1200) -> float | None:
        """Worst-case spend, or None when any connection has no declared prices.

        ``max_tokens`` is the per-call output ceiling, so call cap times that
        ceiling is a true upper bound on output tokens. The input estimate is
        deliberately generous: it is a ceiling, not a measurement.
        """
        total = 0.0
        for connection in self.judges:
            if not connection.prices_declared:
                return None
            calls = max(1, n_items) * max(1, repeats) * max(1, connection.repeats)
            input_usd = calls * estimated_input_tokens / 1_000_000 * float(connection.price_in_per_mtok or 0.0)
            output_usd = calls * connection.max_tokens / 1_000_000 * float(connection.price_out_per_mtok or 0.0)
            total += input_usd + output_usd
        return round(total, 6)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().casefold()).strip("-")
    return (slug or "judge")[:40]


def _base_url_problems(base_url: str) -> list[str]:
    """``base_url`` rules shared with the web app: http(s) only, no credentials."""
    url = (base_url or "").strip()
    if not url:
        return ["base_url is required"]
    parts = urlsplit(url)
    problems: list[str] = []
    if parts.scheme not in {"http", "https"}:
        problems.append("base_url must start with http:// or https:// (no other scheme is allowed)")
    if parts.username or parts.password:
        problems.append(
            "base_url must not embed credentials (user:pass@host); the key belongs in the "
            "environment variable named by key_env"
        )
    if not parts.hostname:
        problems.append("base_url must name a host")
    return problems


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_connections_file(path: Path | str) -> ConnectionsFile:
    """Parse and validate ``connections.yaml``.

    Raises :class:`ConnectionsFileError` with a message that names the offending
    entry and field. A key field anywhere in the file is an error, whichever name
    it uses, so the mistake is caught before anything is called.
    """
    target = Path(path)
    if not target.is_file():
        raise ConnectionsFileError(f"connections file not found: {target}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a dependency
        raise ConnectionsFileError("PyYAML is required to read a connections file") from exc
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConnectionsFileError(f"could not read {target.name}: {exc}") from exc
    if payload is None:
        raise ConnectionsFileError(f"{target.name} is empty")
    if not isinstance(payload, Mapping):
        raise ConnectionsFileError(f"{target.name} must be a mapping with a 'connections' key")
    rows = payload.get("connections")
    if not isinstance(rows, list) or not rows:
        raise ConnectionsFileError(f"{target.name} must contain a non-empty 'connections' list")

    connections: list[FileConnection] = []
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            raise ConnectionsFileError(f"connection #{index} is not a mapping")
        entry = {str(key).strip(): value for key, value in raw.items()}
        lowered = {key.casefold() for key in entry}
        leaked = sorted(field for field in KEY_FIELDS if field in lowered)
        if leaked:
            raise ConnectionsFileError(
                f"connection #{index} contains a key field ({', '.join(leaked)}). A key must never be "
                "written to this file: remove the field and name the environment variable with key_env"
            )
        unknown = sorted(field for field in entry if field not in ALLOWED_FIELDS)
        if unknown:
            raise ConnectionsFileError(f"connection #{index} has unknown field(s): {', '.join(unknown)}")
        if not entry.get("name"):
            raise ConnectionsFileError(f"connection #{index} needs a name")
        key_env = str(entry.get("key_env") or "").strip()
        alias = str(entry.get("api_key_env") or "").strip()
        if key_env and alias and key_env != alias:
            raise ConnectionsFileError(
                f"connection #{index} sets both key_env and api_key_env to different values; "
                "keep one indirection field only"
            )
        key_env = key_env or alias
        connections.append(
            FileConnection(
                name=str(entry["name"]),
                base_url=str(entry.get("base_url") or ""),
                model=str(entry.get("model") or ""),
                key_env=key_env,
                family_label=str(entry.get("family_label") or ""),
                role=str(entry.get("role") or "judge").strip().lower(),
                repeats=_as_int(entry.get("repeats"), 1),
                max_tokens=_as_int(entry.get("max_tokens"), 2048),
                temperature=_as_float(entry.get("temperature"), 0.3),
                call_cap=_as_int(entry.get("call_cap"), 200),
                usd_cap=_as_float(entry.get("usd_cap"), 2.0),
                price_in_per_mtok=_optional_float(entry.get("price_in_per_mtok")),
                price_out_per_mtok=_optional_float(entry.get("price_out_per_mtok")),
                api_style=str(entry.get("api_style") or "chat").strip().lower(),
            )
        )
    parsed = ConnectionsFile(path=target, connections=tuple(connections))
    problems = parsed.problems()
    if problems:
        raise ConnectionsFileError("; ".join(problems))
    return parsed
