"""``medeval doctor`` — check the environment without printing any secret.

Every check prints PASS / WARN / FAIL with an actionable line. The function
never reads a key's value: it only asks the settings object whether one is
configured, and the token-flow check generates a throwaway token in memory.
"""

from __future__ import annotations

import shutil
import socket
import sys

from medeval_core.config import load_settings
from medeval_core.paths import data_dir, repo_root, results_dir

__all__ = ["run_doctor"]

_ROWS: list[tuple[str, str, str]] = []


def _record(name: str, state: str, detail: str) -> None:
    _ROWS.append((name, state, detail))


def _ok(name: str, detail: str) -> None:
    _record(name, "PASS", detail)


def _warn(name: str, detail: str) -> None:
    _record(name, "WARN", detail)


def _fail(name: str, detail: str) -> None:
    _record(name, "FAIL", detail)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _stale_medeval(port: int) -> bool:
    """True when something on the port answers like a medeval app."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            body = response.read(400).decode("utf-8", "replace")
            return '"version"' in body and ("medeval" in body or '"ok"' in body)
    except urllib.error.HTTPError as exc:  # 403 is the token gate: it IS a medeval app
        return exc.code == 403
    except Exception:
        return False


def run_doctor(*, port: int = 8765, host: str = "127.0.0.1", use_env: bool = False) -> int:
    _ROWS.clear()
    print(f"medeval-lab doctor - {sys.platform}, Python {sys.version.split()[0]}")
    print("(no key value is read, printed or stored by this check)\n")

    # 1. Python
    if sys.version_info >= (3, 12):
        _ok("python version", f"{sys.version.split()[0]} (needs 3.12+)")
    else:
        _fail("python version", f"{sys.version.split()[0]} is older than 3.12")

    # 2. Dependencies
    missing = []
    for module in ("fastapi", "uvicorn", "httpx", "jinja2"):
        try:
            __import__(module)
        except Exception:
            missing.append(module)
    if missing:
        _fail("dependencies", "missing: " + ", ".join(missing) + " - run: pip install -e .")
    else:
        _ok("dependencies", "fastapi, uvicorn, httpx import")

    # 3. Repository layout
    root = repo_root()
    for label, path in (("repo root", root), ("data dir", data_dir()), ("results dir", results_dir())):
        if path.is_dir():
            _ok(label, str(path))
        else:
            _fail(label, f"missing: {path}")

    # 4. Port
    if _port_free(port):
        _ok("port", f"{port} is free")
    elif _stale_medeval(port):
        _warn(
            "port",
            f"{port} is held by another medeval-lab server. Stop it with: "
            f"Get-NetTCPConnection -LocalPort {port} -State Listen | "
            "ForEach-Object { Stop-Process -Id $_.OwningProcess }",
        )
    else:
        _warn("port", f"{port} is held by another program; the app will pick the next free port")

    # 5. Token flow
    try:
        from medeval_web.app import create_app, generate_launch_token

        token = generate_launch_token()
        app = create_app(token=token, allowed_hosts={"testserver"}, local_data_dir=data_dir())
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            blocked = client.get("/api/health").status_code
            allowed = client.get("/api/health", headers={"X-Medeval-Token": token}).status_code
        if blocked == 403 and allowed == 200:
            _ok("token flow", "403 without the token, 200 with it")
        else:
            _fail("token flow", f"unexpected statuses: without={blocked} with={allowed}")
    except Exception as exc:
        _fail("token flow", f"{type(exc).__name__}: {exc}")

    # 6. Git
    if (root / ".git").exists():
        import subprocess

        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=False
        )
        dirty = len([line for line in status.stdout.splitlines() if line.strip()])
        if status.returncode != 0:
            _warn("git", "git status failed; is git installed?")
        elif dirty:
            _warn("git", f"{dirty} uncommitted change(s) - commit before sharing results")
        else:
            _ok("git", "clean working tree")
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, capture_output=True, text=True, check=False
        )
        if branch.returncode == 0:
            _ok("git branch", branch.stdout.strip())
    else:
        _warn("git", "no .git directory (running from an extracted zip?)")

    # 7. Ollama (optional local judge)
    try:
        import httpx

        response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.5)
        if response.status_code == 200:
            models = [row.get("name") for row in (response.json().get("models") or [])]
            _ok("ollama", f"reachable; {len(models)} model(s) installed")
        else:
            _warn("ollama", f"answered HTTP {response.status_code}")
    except Exception:
        _warn("ollama", "not reachable (fine unless you want a local judge): install from ollama.com")

    # 8. Settings (booleans only, never values). Settings are read from the real
    # process environment only: doctor must never load a .env file itself, because
    # "no key file is read" is a property worth keeping even for a diagnostic. A
    # .env on disk is reported by name in the check below instead.
    settings = load_settings(dotenv_paths=[])
    _ok("backend setting", f"{settings.backend_override} (mock unless a connection is assigned)")
    if settings.api_key is not None:
        _ok("api key in env var", "present (value not printed)")
    else:
        _ok("api key in env var", "absent - the app takes keys in memory on the Models page")
    _ok("prices", "configured" if settings.input_usd_per_million is not None else "not configured (call cap binds)")
    _ok("retries", f"{settings.retry_attempts} attempt(s), {settings.retry_base_delay_s}s base backoff")
    _ok("timeout / concurrency", f"{settings.timeout_s}s per call, at most {settings.max_concurrency} in flight")

    # 8b. Key-safety checks (v0.5). File NAMES only: no .env content is read here.
    env_files = sorted(
        path.name
        for path in root.glob(".env*")
        if path.is_file() and path.name != ".env.example"
    )
    if env_files:
        _warn(
            "local .env files",
            f"found {', '.join(env_files)} in the repository folder. Keys in a file are keys that can leak: "
            "delete them and revoke them at the provider; the app keeps keys in memory only",
        )
    else:
        _ok("local .env files", "none in the repository folder")
    if use_env:
        _warn(
            "--use-env",
            "ON: the app will load .env into its environment. Prefer the Models page (memory only) "
            "or connections.yaml with key_env, and keep the file out of any shared folder",
        )
    else:
        _ok("--use-env", "off (keys come from the app's memory or an environment variable)")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        _warn(
            "bind address",
            f"{host} is not loopback: this app has a single-user trust model (one launch token, no accounts) "
            "and must not be exposed to a network. Keep 127.0.0.1 unless you really need --allow-remote",
        )
    else:
        _ok("bind address", "127.0.0.1 (loopback only)")

    # 9. Fast self-test
    try:
        from medeval_reliability.agreement import cohen_kappa
        from medeval_study.models import McqItem
        from medeval_study.sampling import normalise_stem

        item = McqItem(
            item_id="doctor-1",
            question="A synthetic self-test question?",
            options={"A": "a", "B": "b", "C": "c", "D": "d"},
            key_letter="A",
            key_text="a",
            dataset="synthetic",
            split="dev",
            row_index=0,
        )
        if not normalise_stem(item.question) or cohen_kappa(["a", "b"], ["a", "b"]) != 1.0:
            raise AssertionError("a metric helper returned an unexpected value")
        _ok("self-test", "core modules import; kappa and item models work")
    except Exception as exc:
        _fail("self-test", f"{type(exc).__name__}: {exc}")

    # 10. Disk space for results
    usage = shutil.disk_usage(results_dir())
    free_gb = usage.free / (1024 ** 3)
    if free_gb >= 1:
        _ok("disk", f"{free_gb:.1f} GB free for results")
    else:
        _warn("disk", f"only {free_gb:.2f} GB free")

    print(f"{'check':<28}{'state':<7}detail")
    print("-" * 88)
    for name, state, detail in _ROWS:
        print(f"{name:<28}{state:<7}{detail}")
    failures = sum(1 for _, state, _ in _ROWS if state == "FAIL")
    warnings = sum(1 for _, state, _ in _ROWS if state == "WARN")
    print("-" * 88)
    print(f"{failures} failure(s), {warnings} warning(s)")
    if failures:
        print("Fix the failures above before starting a run.")
    else:
        print("Environment looks usable. Next: medeval ui")
    return 1 if failures else 0
