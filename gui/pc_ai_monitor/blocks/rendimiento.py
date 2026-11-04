"""Tarjeta "Rendimiento del motor": métricas vivas del motor Gufo.

Peer de ``engine.py``: toda decisión de renderizado vive en
``rendimiento_logica`` (testeada sin GTK); este widget solo lee las fuentes
y pinta. Las lecturas HTTP al motor (``/metrics``, ``/v1/models``,
``/slots``) y el subproceso acotado ``gufo-flash status`` corren en un
worker; el hilo principal de GTK nunca se bloquea. Nunca ``podman`` ni
``systemctl`` desde acá.

Una lectura fallida muestra el error y conserva los últimos valores
conocidos marcados como desactualizados: nunca borra la tarjeta.
"""

# pyright: reportAttributeAccessIssue=false

import http.client
import subprocess
import threading
import time

from gi.repository import GLib, Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.blocks.rendimiento_logica import (
    ENGINE_HOST,
    ENGINE_PORT,
    GUFO_FLASH_TIMEOUT_S,
    Metrics,
    RendimientoView,
    SpeedTracker,
    build_view,
    footer_text,
    format_timestamp,
    parse_guard,
    parse_metrics,
    parse_served_context,
    parse_slots,
    stale_view,
)
from pc_ai_monitor.datos import Snapshot

HTTP_TIMEOUT_S = 3.0
REFRESH_INTERVAL_S = 5.0
_GUFO_FLASH = "gufo-flash"


class RendimientoBlock(Block):
    """Rendimiento y salud del motor Gufo leyendo solo al motor mismo."""

    title = "RENDIMIENTO DEL MOTOR"
    frame = True

    def __init__(self) -> None:
        super().__init__()
        self._view = RendimientoView()
        self._refreshing = False
        self._last_refresh = 0.0
        # Contadores del propio motor: los deltas entre lecturas dan tok/s.
        self._prefill = SpeedTracker()
        self._decode = SpeedTracker()

        self._rows_label = Gtk.Label(xalign=0)
        self._rows_label.add_css_class("caption")
        self._rows_label.set_wrap(True)
        self._rows_label.set_max_width_chars(55)
        self.body.append(self._rows_label)

        self._warning_label = Gtk.Label(xalign=0)
        self._warning_label.add_css_class("error")
        self._warning_label.set_wrap(True)
        self._warning_label.set_max_width_chars(55)
        self._warning_label.set_visible(False)
        self.body.append(self._warning_label)

        self._error_label = Gtk.Label(xalign=0)
        self._error_label.add_css_class("error")
        self._error_label.set_wrap(True)
        self._error_label.set_max_width_chars(55)
        self._error_label.set_visible(False)
        self.body.append(self._error_label)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._refresh_button = Gtk.Button(label="Actualizar")
        self._refresh_button.connect("clicked", self._on_refresh_clicked)
        actions.append(self._refresh_button)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        actions.append(self._spinner)

        self._footer_label = Gtk.Label(xalign=0)
        self._footer_label.add_css_class("muted")
        self._footer_label.set_wrap(True)
        self._footer_label.set_hexpand(True)
        actions.append(self._footer_label)
        self.body.append(actions)

        self._render()
        self._refresh(force=True)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        """Aprovecha el pulso para sondear cada ~5 s."""
        self._refresh(force=False)

    # -- lectura ------------------------------------------------------------

    def _refresh(self, force: bool) -> None:
        now = time.monotonic()
        if self._refreshing:
            return
        if not force and now - self._last_refresh < REFRESH_INTERVAL_S:
            return
        self._refreshing = True
        self._last_refresh = now
        self._render()

        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        metrics_text = _http_text("/metrics")
        if metrics_text is None:
            GLib.idle_add(
                self._on_read_failed,
                f"el motor no responde en :{ENGINE_PORT}",
            )
            return
        metrics = parse_metrics(metrics_text)

        served = parse_served_context(_http_text("/v1/models") or "")
        slots = parse_slots(_http_text("/slots") or "")

        stdout, returncode, failure = _run_gufo_flash_status()
        guard = parse_guard(stdout, returncode, failure)

        # Deltas de los contadores del motor, en el propio worker: como el
        # gate de _refreshing impide solapar workers, acá no hay carrera.
        prefill_source = (
            metrics.prompt_tokens_processed_total
            if metrics.prompt_tokens_processed_total is not None
            else metrics.prompt_tokens_total
        )
        self._prefill.update(prefill_source, time.monotonic())
        self._decode.update(metrics.tokens_predicted_total, time.monotonic())

        GLib.idle_add(
            self._on_read_done, metrics, served, slots, guard
        )

    def _on_read_done(
        self,
        metrics: Metrics,
        served: int | None,
        slots,
        guard,
    ) -> bool:
        self._refreshing = False
        if served is None and slots and slots[0].n_ctx is not None:
            served = slots[0].n_ctx
        self._view = build_view(
            metrics=metrics,
            served_context=served,
            slots=slots,
            guard=guard,
            prefill_rate=self._prefill.last,
            decode_rate=self._decode.last,
            last_read=format_timestamp(time.time()),
        )
        self._render()
        return False

    def _on_read_failed(self, error: str) -> bool:
        self._refreshing = False
        self._view = stale_view(self._view, error)
        self._render()
        return False

    def _on_refresh_clicked(self, _button: Gtk.Button) -> None:
        self._refresh(force=True)

    # -- pintado ------------------------------------------------------------

    def _render(self) -> None:
        view = self._view

        self._rows_label.set_text(
            "\n".join(f"{label}: {value}" for label, value in view.rows)
        )

        self._warning_label.set_text(view.warning)
        self._warning_label.set_visible(bool(view.warning))

        self._error_label.set_text(view.error)
        self._error_label.set_visible(bool(view.error))

        self._footer_label.set_text(footer_text(view))

        self._refresh_button.set_sensitive(not self._refreshing)
        if self._refreshing:
            self._spinner.start()
            self._spinner.set_visible(True)
        else:
            self._spinner.stop()
            self._spinner.set_visible(False)


def _http_text(path: str, timeout: float = HTTP_TIMEOUT_S) -> str | None:
    """GET de solo lectura al motor, en su host y puerto fijos; nunca propaga."""
    connection = http.client.HTTPConnection(
        ENGINE_HOST, ENGINE_PORT, timeout=timeout
    )
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            return None
        return response.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        connection.close()


def _run_gufo_flash_status() -> tuple[str, int | None, str | None]:
    """Subproceso acotado a ``gufo-flash status``, solo desde un worker."""
    try:
        completed = subprocess.run(
            [_GUFO_FLASH, "status"],
            capture_output=True,
            text=True,
            timeout=GUFO_FLASH_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return "", None, "missing"
    except subprocess.TimeoutExpired:
        return "", None, "timeout"
    except OSError as exc:
        return "", None, f"oserror ({exc})"

    return completed.stdout, completed.returncode, None
