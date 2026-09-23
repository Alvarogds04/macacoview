"""Data layer.

The GUI must never block: every collector runs in a worker thread and results
reach the GTK main loop through ``GLib.idle_add``. Each source is independent,
so a failing collector degrades only its own section.
"""

# PyGObject has no type stubs of its own; gui/typings/gi provides permissive ones
# and pc_ai_monitor/__init__ calls require_version before this import runs.
# pyright: reportAttributeAccessIssue=false

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gi.repository import GLib

from pc_ai_monitor.config import Config

STATS_TIMEOUT_S = 5.0
PORTS_TIMEOUT_S = 10.0
TOKENS_TIMEOUT_S = 45.0
# El router local agrega sistema, motores, cloud, Jev y sesiones. Su /status hace
# unas pocas llamadas locales, asi que 3s sobran y sigue siendo no bloqueante.
ROUTER_TIMEOUT_S = 3.0
ROUTER_STATUS_URL = "http://127.0.0.1:11009/status"
ROUTER_METRICS_URL = "http://127.0.0.1:11009/metrics"

# Ordered like the watch entries: pi, hermes, firefox, system, other. Single
# source of truth: the [[watch]] defaults in config, so a renamed default entry
# renames the history columns with it.
GROUP_KEYS = tuple(entry.name for entry in Config().watch)


@dataclass(frozen=True)
class Sample:
    used_gib: float
    available_gib: float
    groups: tuple[float, ...]


@dataclass(frozen=True)
class Snapshot:
    stats: dict[str, Any] | None = None
    tokens: dict[str, Any] | None = None
    ports: str | None = None
    router_status: dict[str, Any] | None = None
    router_metrics: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()
    history: tuple[Sample, ...] = ()

    def memory(self) -> dict[str, Any]:
        return (self.stats or {}).get("memory", {})

    def router(self) -> dict[str, Any]:
        """Estado del router, motores, cloud, Jev y sesiones."""
        return self.router_status or {}

    def telemetry(self) -> dict[str, Any]:
        """Agregados de decision: porcentajes, conteos, gasto estimado de Jev."""
        return (self.router_metrics or {}).get("telemetry", {})

    def skills_budget(self) -> dict[str, Any]:
        return (self.router_metrics or {}).get("skills_budget", {})

    def recent_routes(self) -> list[dict[str, Any]]:
        return (self.router_metrics or {}).get("recent", []) or []

    def models(self) -> list[dict[str, Any]]:
        return (self.stats or {}).get("models", [])

    def groups(self) -> dict[str, Any]:
        return (self.stats or {}).get("groups", {})

    def processes(self) -> list[dict[str, Any]]:
        return (self.stats or {}).get("processes", [])

    def tokens_section(self, name: str) -> dict[str, Any]:
        return (self.tokens or {}).get(name, {})


def _run(argv: list[str], timeout: float) -> tuple[str | None, str | None]:
    """Run a collector. Returns (stdout, error) and never raises."""
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None, f"no existe: {argv[0]}"
    except subprocess.TimeoutExpired:
        return None, f"timeout de {timeout:.0f}s: {argv[0]}"
    except OSError as error:
        return None, f"{argv[0]}: {error}"

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        return (
            None,
            f"{argv[0]} salió con {completed.returncode}: {detail[-1] if detail else '-'}",
        )

    return completed.stdout, None


def _get_json(url: str, timeout: float) -> dict[str, Any] | None:
    """GET JSON local. Devuelve None ante cualquier fallo: nunca propaga."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


class Collector:
    """Polls the enabled sources in a worker thread and publishes snapshots."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._subscribers: list[Callable[[Snapshot], None]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._stats: dict[str, Any] | None = None
        self._ports: str | None = None
        self._tokens: dict[str, Any] | None = None
        self._router_status: dict[str, Any] | None = None
        self._router_metrics: dict[str, Any] | None = None
        self._errors: dict[str, str] = {}
        self._history: deque[Sample] = deque(maxlen=config.history_capacity)

    # -- public API -------------------------------------------------------

    def subscribe(self, callback: Callable[[Snapshot], None]) -> None:
        self._subscribers.append(callback)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="collector", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(
                stats=self._stats,
                tokens=self._tokens,
                ports=self._ports,
                router_status=self._router_status,
                router_metrics=self._router_metrics,
                errors=tuple(
                    f"{key}: {value}" for key, value in sorted(self._errors.items())
                ),
                history=tuple(self._history),
            )

    # -- worker -----------------------------------------------------------

    def _loop(self) -> None:
        next_stats = next_ports = next_tokens = next_router = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            collected = False

            if now >= next_stats:
                next_stats = now + self._config.stats_interval_s
                collected |= self._collect_stats()
            if now >= next_ports:
                next_ports = now + self._config.ports_interval_s
                collected |= self._collect_ports()
            if now >= next_tokens:
                next_tokens = now + self._config.tokens_interval_s
                collected |= self._collect_tokens()
            if now >= next_router:
                next_router = now + self._config.stats_interval_s
                collected |= self._collect_router()

            if collected:
                self._publish()

            self._stop.wait(0.2)

    def _collect_router(self) -> bool:
        """Lee el router local. Si esta parado, la seccion queda vacia, no rompe."""
        status = _get_json(ROUTER_STATUS_URL, ROUTER_TIMEOUT_S)
        metrics = _get_json(ROUTER_METRICS_URL, ROUTER_TIMEOUT_S)

        with self._lock:
            self._router_status = status
            self._router_metrics = metrics
            if status is None:
                if self._config.bar_router_indicators:
                    self._errors["router"] = "router no responde en :11009"
                else:
                    # Opt-in: a box without the local router is not failing, it
                    # never asked for this block. Report it only if it was asked.
                    self._errors.pop("router", None)
            else:
                self._errors.pop("router", None)
        return True

    def _collect_stats(self) -> bool:
        if not self._config.stats_bin.exists():
            self._set_error("recursos", f"falta {self._config.stats_bin.name}")
            return True

        stdout, error = _run([str(self._config.stats_bin)], STATS_TIMEOUT_S)
        if error is not None:
            self._set_error("recursos", error)
            return True

        try:
            stats = json.loads(stdout or "")
        except json.JSONDecodeError as parse_error:
            self._set_error("recursos", f"JSON inválido: {parse_error}")
            return True

        with self._lock:
            self._stats = stats
            self._history.append(self._sample_from(stats))
            self._errors.pop("recursos", None)
        return True

    def _collect_ports(self) -> bool:
        helper = self._config.ports_helper
        # The helper exists to see other users' sockets, which needs root. Without
        # it `ss` still answers for the current user, so a machine that never
        # installed the privileged helper is degraded, not broken -- and must not
        # report an error on every tick.
        if not helper.exists():
            argv = ["ss", "-tulpn"]
        else:
            argv = (
                ["sudo", "-n", str(helper)]
                if self._config.ports_use_sudo
                else [str(helper)]
            )
        stdout, error = _run(argv, PORTS_TIMEOUT_S)
        if error is not None:
            self._set_error("puertos", error)
            return True

        with self._lock:
            self._ports = stdout
            self._errors.pop("puertos", None)
        return True

    def _collect_tokens(self) -> bool:
        if not self._config.tokens_bin.exists():
            self._set_error("tokens", f"falta {self._config.tokens_bin.name}")
            return True

        stdout, error = _run([str(self._config.tokens_bin)], TOKENS_TIMEOUT_S)
        if error is not None:
            self._set_error("tokens", error)
            return True

        try:
            tokens = json.loads(stdout or "")
        except json.JSONDecodeError as parse_error:
            self._set_error("tokens", f"JSON inválido: {parse_error}")
            return True

        with self._lock:
            self._tokens = tokens
            self._errors.pop("tokens", None)
        return True

    def _set_error(self, key: str, message: str) -> None:
        with self._lock:
            self._errors[key] = message

    @staticmethod
    def _sample_from(stats: dict[str, Any]) -> Sample:
        memory = stats.get("memory", {})
        groups = stats.get("groups", {})

        def group_value(name: str) -> float:
            entry = groups.get(name) or {}
            try:
                return float(entry.get("rss_gib") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def memory_value(name: str) -> float:
            try:
                return float(memory.get(name) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return Sample(
            used_gib=memory_value("used_gib"),
            available_gib=memory_value("available_gib"),
            groups=tuple(group_value(name) for name in GROUP_KEYS),
        )

    def _publish(self) -> None:
        snapshot = self.snapshot()
        for callback in list(self._subscribers):
            GLib.idle_add(self._deliver, callback, snapshot)

    @staticmethod
    def _deliver(callback: Callable[[Snapshot], None], snapshot: Snapshot) -> bool:
        # Exceptions in a page must not take down the collector thread; GLib
        # would just log them, so keep the callback contract simple.
        callback(snapshot)
        return False
