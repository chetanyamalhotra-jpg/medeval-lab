"""Launch the local web app on loopback and open a browser.

The server is a normal blocking uvicorn process: ``medeval ui`` stays in the
foreground so the user can stop it with Ctrl+C, and the browser is opened once the
socket is accepting connections. Nothing is served beyond ``127.0.0.1``.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import webbrowser
from typing import Any

__all__ = ["serve"]

LOOPBACK = "127.0.0.1"


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _looks_like_medeval(host: str, port: int) -> bool:
    """True when the port answers like a medeval app (the token gate counts)."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2) as response:
            body = response.read(400).decode("utf-8", "replace")
            return '"version"' in body and ("medeval" in body or '"ok"' in body)
    except urllib.error.HTTPError as exc:  # 403 = token gate: definitely medeval
        return exc.code == 403
    except Exception:
        return False


def explain_busy_port(host: str, port: int) -> None:
    """Say plainly what holds a busy port, so 'token required' is never a dead end.

    A stale medeval server on the port is the classic dead end: the page loads but
    every API call 403s because the token belongs to the *old* launch. The fix is
    to stop that process, and this prints the exact PowerShell to do it.
    """
    if _looks_like_medeval(host, port):
        print(
            f"[ui] port {port} is held by ANOTHER medeval-lab server from an earlier launch.\n"
            f"[ui] That is why a page on it says the launch token is missing or invalid.\n"
            f"[ui] Stop it with:\n"
            f'[ui]   Get-NetTCPConnection -LocalPort {port} -State Listen | '
            "ForEach-Object { Stop-Process -Id $_.OwningProcess }\n"
            f"[ui] (or press Ctrl+C in the terminal running it), then start the app again."
        )
    else:
        print(f"[ui] port {port} is used by another program; picking the next free port.")


def pick_port(host: str, preferred: int, attempts: int = 20) -> int:
    """Return ``preferred`` if it is free, otherwise the next free port."""
    if not _port_free(host, preferred):
        explain_busy_port(host, preferred)
    for offset in range(attempts):
        candidate = preferred + offset
        if _port_free(host, candidate):
            return candidate
    raise RuntimeError(f"no free port in {preferred}-{preferred + attempts - 1}")


def _open_when_ready(url: str, host: str, port: int, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.2)


def _local_ipv4_addresses() -> set[str]:
    """Every IPv4 address of this machine, for the Host check when binding wide."""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    return addresses


def serve(
    *,
    host: str = LOOPBACK,
    port: int = 8765,
    open_browser: bool = True,
    use_env: bool = False,
    allow_remote: bool = False,
) -> int:
    """Run the app until interrupted. Returns a process exit code.

    ``use_env`` opts into loading the git-ignored ``.env`` (``medeval ui --use-env``).
    It is off by default because keys come from the Models page and live in
    memory only; ``.env`` support is kept for the v0.2-style environment setup.

    The app is loopback-only. A non-loopback ``host`` is refused unless
    ``allow_remote`` is set, and then it prints a loud warning: the trust model is
    one user on one machine (a single launch token, no accounts, no TLS), so
    binding it to a network exposes every page and API to anyone who can reach
    the port. The launch token, Host and Origin checks stay active either way.
    """
    import uvicorn

    from medeval_core.config import load_settings

    from .app import create_app, generate_launch_token

    if host not in {"127.0.0.1", "localhost", "::1"}:
        if not allow_remote:
            print(f"[ui] refusing to bind {host}: this app is loopback-only.")
            print("[ui] Start it on 127.0.0.1 (the default). If you really need another interface,")
            print("[ui] re-run with --allow-remote and understand the warning it prints.")
            return 2
        print("!" * 78)
        print(f"[ui] WARNING: binding to {host}, which is NOT loopback.")
        print("[ui] This app has a single-user trust model: one launch token, no accounts, no TLS.")
        print("[ui] Anyone who can reach this port can read and drive it. Do not expose it to a network,")
        print("[ui] and do not use it as a shared service. Stop it with Ctrl+C as soon as you are done.")
        print("!" * 78)
    chosen = pick_port(host, port)
    if chosen != port:
        print(f"[ui] port {port} was busy; using {chosen}")

    # One random token per launch. The browser receives it in the URL fragment,
    # keeps it in sessionStorage, and sends it as X-Medeval-Token; the fragment
    # is never sent to the server, so it does not appear in any log.
    token = generate_launch_token()
    allowed_hosts = {f"127.0.0.1:{chosen}", f"localhost:{chosen}"}
    if host == "::1":
        allowed_hosts.add(f"[::1]:{chosen}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        # Keep the Host check active: the names the machine can legitimately be
        # reached by are the only ones accepted.
        allowed_hosts.add(f"{host}:{chosen}")
        if host in {"0.0.0.0", "::"}:
            allowed_hosts.update(f"{address}:{chosen}" for address in _local_ipv4_addresses())
    scope = "loopback only" if host in {"127.0.0.1", "localhost", "::1"} else f"ALL interfaces ({host}) - do not expose"
    url = f"http://{host}:{chosen}/#token={token}"
    print(f"[ui] medeval-lab serving at http://{host}:{chosen}/ ({scope})")
    print(f"[ui] launch token: {token}")
    print("[ui] the browser opens with the token in the address bar; keep the tab open")
    print("[ui] press Ctrl+C to stop; any stored keys are forgotten when the app stops")
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, host, chosen), daemon=True).start()

    settings = load_settings(dotenv_paths=None if use_env else [])
    app: Any = create_app(token=token, allowed_hosts=allowed_hosts, settings=settings)
    keys = app.state.key_store
    exit_code = 0
    try:
        with contextlib.suppress(KeyboardInterrupt):  # pragma: no cover - interactive path
            uvicorn.run(app, host=host, port=chosen, log_level="warning")
    finally:
        forgotten = keys.forget_all()
        if forgotten:
            print(f"[ui] forgot {forgotten} stored key(s)")
    return exit_code
