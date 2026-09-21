"""Router, Jev, cloud, Engram y sesiones: las secciones que anade el router.

Cada bloque lee del Snapshot que ya recolecta el hilo de trabajo, asi que la UI
nunca se bloquea. Si el router esta parado, los bloques muestran guiones en vez de
romper el panel.

Umbrales visuales (seccion 20 del encargo):
  MemAvailable >= 12 GiB OK · 10-12 WARNING · <10 CRITICAL
  Flash guard disparado CRITICAL · Jev caido WARNING · LiteLLM caido CRITICAL
"""

# pyright: reportAttributeAccessIssue=false

from gi.repository import Gtk

from pc_ai_monitor.blocks import Block
from pc_ai_monitor.datos import Snapshot
from pc_ai_monitor.formato import count, gib, money, safe, tokens

DASH = "\u2014"


def _dot(ok: bool) -> str:
    return "\u25cf" if ok else "\u25cb"


def _tier_class(level: str) -> str:
    return {
        "OK": "tier-ok",
        "WARNING": "tier-warn",
        "CRITICAL": "tier-crit",
    }.get(level, "muted")


class KeyValue(Gtk.Grid):
    """Rejilla de pares clave/valor; el bloque la rellena en cada refresco."""

    def __init__(self, columns: int = 2) -> None:
        super().__init__()
        self.set_column_spacing(18)
        self.set_row_spacing(3)
        self._rows: list[Gtk.Widget] = []
        self._columns = max(1, columns)

    def clear(self) -> None:
        for child in self._rows:
            self.remove(child)
        self._rows = []
        self._index = 0

    def add(self, key: str, value: str, css: str | None = None) -> None:
        label = Gtk.Label(label=key, xalign=0)
        label.add_css_class("muted")
        text = Gtk.Label(label=value, xalign=0)
        text.add_css_class("mono")
        if css:
            text.add_css_class(css)
        col = self._index % self._columns
        row = self._index // self._columns
        self.attach(label, col * 2, row, 1, 1)
        self.attach(text, col * 2 + 1, row, 1, 1)
        self._rows.extend((label, text))
        self._index += 1

    def reset(self) -> None:
        self.clear()
        self._index = 0


class RouterBlock(Block):
    title = "ROUTER"

    def __init__(self) -> None:
        super().__init__()
        self._grid = KeyValue()
        self._routes = Gtk.Label(label=DASH, xalign=0)
        self._routes.add_css_class("mono")
        self.body.append(self._grid)
        self.body.append(Gtk.Separator())
        self.body.append(self._routes)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        telemetry = snapshot.telemetry()
        recent = snapshot.recent_routes()
        grid = self._grid
        grid.reset()

        if not router:
            grid.add("estado", "router no responde en :11009", "tier-crit")
            self._routes.set_text(DASH)
            return

        info = router.get("router", {})
        shadow = bool(info.get("shadow_mode"))
        grid.add("salud", f"{_dot(True)} vivo", "tier-ok")
        grid.add("versión", str(info.get("version", DASH)))
        grid.add("modo", "shadow (no manda)" if shadow else "ACTIVO", "tier-warn" if shadow else "tier-ok")
        grid.add("sesiones activas", count(info.get("active_sessions")))
        grid.add("confianza mínima", str(info.get("confidence_min", DASH)))
        grid.add("uptime", f"{gib(info.get('uptime_s'), 0)}s" if info.get("uptime_s") else DASH)
        grid.add("decisiones totales", count(telemetry.get("total_decisions")))

        if recent:
            last = recent[0]
            grid.add("última ruta", str(last.get("chosen") or DASH))
            grid.add("última confianza", str(last.get("confidence") or DASH))
            grid.add("latencia última", f"{gib(last.get('total_latency_ms'), 0)} ms")
            grid.add("decidió", str(last.get("decider") or DASH))

        grid.add("% local", f"{telemetry.get('pct_local', 0)}%")
        grid.add("% cloud", f"{telemetry.get('pct_cloud', 0)}%")
        grid.add("% cola", f"{telemetry.get('pct_queue', 0)}%")
        grid.add("latencia media", f"{telemetry.get('avg_routing_latency_ms') or DASH} ms")
        grid.add("confianza media", str(telemetry.get("avg_confidence") or DASH))

        counts = telemetry.get("by_route") or {}
        text = "  ".join(f"{k}={v}" for k, v in sorted(counts.items())) or DASH
        self._routes.set_text(f"rutas: {text}")


class EnginesBlock(Block):
    title = "ABITO / FLASH"

    def __init__(self) -> None:
        super().__init__()
        self._grid = KeyValue(columns=2)
        self.body.append(self._grid)

    def _add(self, grid: KeyValue, name: str, data: dict) -> None:
        healthy = bool(data.get("healthy"))
        grid.add(f"{name} salud", f"{_dot(healthy)} {'vivo' if healthy else 'parado'}",
                 "tier-ok" if healthy else "tier-crit")
        grid.add(f"{name} puerto", str(data.get("port", DASH)))
        ctx = safe(data.get("ctx"))
        useful = safe(data.get("useful_ctx"))
        grid.add(f"{name} ctx", f"{int(ctx)} (útil {int(useful)})")
        grid.add(f"{name} slots", f"{data.get('slot_busy', 0)}/{data.get('slots', 0)} ocupados")
        grid.add(f"{name} cola", count(data.get("queue")))
        prompt = safe(data.get("last_prompt_tokens"))
        grid.add(f"{name} prompt", tokens(int(prompt)) if prompt else DASH)
        mtp = data.get("mtp_acceptance")
        grid.add(f"{name} MTP", f"{mtp:.3f}" if isinstance(mtp, (int, float)) else DASH)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        grid = self._grid
        grid.reset()
        if not router:
            grid.add("estado", "sin datos del router", "tier-crit")
            return
        self._add(grid, "Abito", router.get("abito", {}))
        self._add(grid, "Flash", router.get("flash", {}))


class SystemBlock(Block):
    title = "SYSTEM"

    def __init__(self) -> None:
        super().__init__()
        self._grid = KeyValue()
        self.body.append(self._grid)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        grid = self._grid
        grid.reset()
        if not router:
            grid.add("estado", "sin datos del router", "tier-crit")
            return
        system = router.get("system", {})
        level = str(system.get("mem_level", "UNKNOWN"))
        grid.add("MemAvailable", f"{system.get('mem_available_gib', 0)} GiB", _tier_class(level))
        grid.add("MemTotal", f"{system.get('mem_total_gib', 0)} GiB")
        grid.add("swap en uso", f"{system.get('swap_used_gib', 0)} GiB")
        grid.add("swap libre", f"{system.get('swap_free_gib', 0)} GiB")
        grid.add("PSI some avg10", str(system.get("psi_some_avg10", 0)))
        grid.add("PSI full avg10", str(system.get("psi_full_avg10", 0)))
        fired = bool(system.get("guard_fired"))
        grid.add("flash guard",
                 "DISPARADO" if fired else f"vigilando (<{system.get('guard_threshold_gib', 10)} GiB)",
                 "tier-crit" if fired else "tier-ok")
        alerts = system.get("alerts") or []
        grid.add("alertas", " · ".join(alerts) if alerts else "ninguna")


class JevBlock(Block):
    title = "JEV"

    def __init__(self) -> None:
        super().__init__()
        self._grid = KeyValue()
        self.body.append(self._grid)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        telemetry = snapshot.telemetry()
        recent = snapshot.recent_routes()
        grid = self._grid
        grid.reset()
        if not router:
            grid.add("estado", "sin datos del router", "tier-crit")
            return
        info = router.get("jev", {})
        has_key = bool(info.get("key_present"))
        grid.add("estado", f"{_dot(has_key)} {'con clave' if has_key else 'sin clave'}",
                 "tier-ok" if has_key else "tier-warn")
        if not has_key:
            grid.add("nota", "routing determinista sigue funcionando", "tier-warn")
        grid.add("endpoint", str(info.get("endpoint", DASH)))
        grid.add("modelo", str(info.get("model", DASH)))
        grid.add("umbral confianza", str(info.get("confidence_min", DASH)))

        if recent:
            last = recent[0]
            tokens_last = last.get("jev_input_tokens")
            grid.add("tokens última decisión", count(tokens_last) if tokens_last else DASH)
            grid.add("latencia última", f"{last.get('jev_latency_ms') or DASH} ms")

        total = telemetry.get("jev_input_tokens_total", 0) or 0
        grid.add("tokens acumulados", count(total))
        grid.add("errores", count(telemetry.get("jev_errors")))
        cost = telemetry.get("jev_spend_estimate_usd", 0) or 0
        grid.add("coste estimado", f"{money(cost)} USD (local)")
        grid.add("fórmula", "input_tokens × 42 / 1e9")


class CloudBlock(Block):
    title = "CLOUD"

    def __init__(self) -> None:
        super().__init__()
        self._grid = KeyValue()
        self.body.append(self._grid)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        grid = self._grid
        grid.reset()
        if not router:
            grid.add("estado", "sin datos del router", "tier-crit")
            return
        cloud = router.get("cloud", {})
        nan = bool(cloud.get("nan_configured"))
        oai = bool(cloud.get("openai_configured"))
        llm = bool(cloud.get("litellm_healthy"))
        grid.add("NAN", f"{_dot(nan)} {'configurado' if nan else 'no configurado'}",
                 "tier-ok" if nan else "tier-warn")
        grid.add("OpenAI", f"{_dot(oai)} {'configurado' if oai else 'sin credencial'}",
                 "tier-ok" if oai else "muted")
        if cloud.get("openai_note"):
            grid.add("nota OpenAI", str(cloud["openai_note"]), "muted")
        grid.add("LiteLLM", f"{_dot(llm)} {'vivo' if llm else 'caído'}",
                 "tier-ok" if llm else "tier-crit")
        aliases = cloud.get("litellm_models") or []
        grid.add("aliases publicados", count(len(aliases)))

        recent = snapshot.recent_routes()
        fallbacks = [r for r in recent if str(r.get("chosen") or "").startswith(("nan-", "openai-"))]
        grid.add("últimos desbordes", count(len(fallbacks)))


class EngramBlock(Block):
    title = "ENGRAM"

    def __init__(self) -> None:
        super().__init__()
        self._grid = KeyValue()
        self.hint = Gtk.Label(label="memoria del agente; no es telemetría", xalign=0)
        self.hint.add_css_class("muted")
        self.body.append(self._grid)
        self.body.append(self.hint)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        grid = self._grid
        grid.reset()
        if not router:
            grid.add("estado", "sin datos del router", "tier-crit")
            return
        engram = router.get("engram") or {}
        reachable = bool(engram.get("reachable"))
        service = bool(engram.get("service_active"))
        grid.add("MCP alcanzable", f"{_dot(reachable)} {'sí' if reachable else 'no'}",
                 "tier-ok" if reachable else "tier-warn")
        grid.add("servicio", f"{_dot(service)} {'activo' if service else 'parado'}",
                 "tier-ok" if service else "tier-warn")
        grid.add("endpoint", str(engram.get("endpoint", DASH)))
        grid.add("versión", str(engram.get("version") or DASH))
        grid.add("estado", str(engram.get("status") or DASH))
        # Deliberadamente NO se muestran memorias: son privadas.


class ConfirmationsBlock(Block):
    """Preguntas pendientes: el router nunca sale a cloud ni toca los motores solo."""

    title = "CONFIRMACIONES"

    def __init__(self) -> None:
        super().__init__()
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.body.append(self._list)
        self._rows: list[Gtk.Widget] = []

    def update_snapshot(self, snapshot: Snapshot) -> None:
        for child in self._rows:
            self._list.remove(child)
        self._rows = []

        router = snapshot.router()
        pending = (router.get("pending_confirmations") or []) if router else []
        if not pending:
            label = Gtk.Label(label="nada pendiente de tu decisión", xalign=0)
            label.add_css_class("muted")
            self._list.append(label)
            self._rows.append(label)
            return

        for item in pending:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            head = Gtk.Label(xalign=0)
            head.set_markup(
                f"<b>{item.get('kind', '?')}</b>   "
                f"<span foreground='#d0a030'>{item.get('id')} · "
                f"caduca en {int(safe(item.get('expires_in_s')))}s</span>"
            )
            box.append(head)
            reason = Gtk.Label(label=str(item.get("reason") or ""), xalign=0)
            reason.set_wrap(True)
            reason.add_css_class("muted")
            box.append(reason)
            for option in item.get("options") or []:
                row = Gtk.Label(xalign=0)
                row.set_markup(
                    f"<tt>  {option.get('route', '?'):<20}</tt> {option.get('note', '')}"
                )
                row.add_css_class("mono")
                box.append(row)
            default = item.get("default")
            if default:
                foot = Gtk.Label(xalign=0)
                foot.set_markup(f"<span foreground='#5C6170'>  sin respuesta: <tt>{default}</tt></span>")
                box.append(foot)
            self._list.append(box)
            self._rows.append(box)


class FlashLifecycleBlock(Block):
    """El ciclo de vida de Flash: reglas mecánicas, sin LLM."""

    title = "CICLO DE VIDA DE FLASH"

    def __init__(self) -> None:
        super().__init__()
        self._grid = KeyValue()
        self.body.append(self._grid)

    def update_snapshot(self, snapshot: Snapshot) -> None:
        router = snapshot.router()
        grid = self._grid
        grid.reset()
        if not router:
            grid.add("estado", "sin datos del router", "tier-crit")
            return
        lc = router.get("flash_lifecycle") or {}
        state = str(lc.get("state", DASH))
        grid.add("estado", state, "tier-ok" if state == "ready" else "muted")
        grid.add("descarga tras", f"{lc.get('idle_unload_minutes', DASH)} min ocioso")
        grid.add("ocioso ahora", f"{gib(lc.get('idle_s'), 0)}s")
        grid.add("libre ahora", f"{lc.get('mem_available_gib', DASH)} GiB")
        grid.add("coste de Flash", f"{lc.get('flash_cost_gib', DASH)} GiB")
        left = lc.get("would_leave_gib", 0)
        margin = lc.get("margin_gib", 0)
        grid.add("quedarían", f"{left} GiB (margen exigido {margin})",
                 "tier-ok" if lc.get("can_load") else "tier-warn")
        grid.add("¿se puede autocargar?",
                 "sí" if lc.get("can_load") else "no: haría falta tu decisión")
        grid.add("autocarga / autodescarga",
                 f"{'sí' if lc.get('auto_load') else 'no'} / {'sí' if lc.get('auto_unload') else 'no'}")
        if lc.get("shadow_mode"):
            grid.add("modo", "shadow: no acciona, solo informa", "tier-warn")
        grid.add("cargas / descargas", f"{lc.get('loads', 0)} / {lc.get('unloads', 0)}")
        grid.add("último evento", str(lc.get("last_event") or DASH))


class SessionsBlock(Block):
    title = "SESSIONS"

    def __init__(self) -> None:
        super().__init__()
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.body.append(self._list)
        self._rows: list[Gtk.Widget] = []

    def update_snapshot(self, snapshot: Snapshot) -> None:
        for child in self._rows:
            self._list.remove(child)
        self._rows = []

        router = snapshot.router()
        sessions = (router.get("sessions") or []) if router else []
        if not sessions:
            label = Gtk.Label(label="sin sesiones activas", xalign=0)
            label.add_css_class("muted")
            self._list.append(label)
            self._rows.append(label)
            return

        header = Gtk.Label(xalign=0)
        header.set_markup(
            "<tt>id            workflow     ruta           ctx      edad   cola</tt>"
        )
        header.add_css_class("muted")
        self._list.append(header)
        self._rows.append(header)

        for session in sessions[:12]:
            row = Gtk.Label(xalign=0)
            row.set_markup(
                "<tt>{sid:<12}  {wf:<11}  {route:<12}  {ctx:>6}  {age:>5}s  {queue}</tt>".format(
                    sid=str(session.get("short_id") or session.get("session_id") or DASH)[:12],
                    wf=str(session.get("workflow") or DASH)[:11],
                    route=str(session.get("route") or DASH)[:12],
                    ctx=count(session.get("estimated_context")),
                    age=int(safe(session.get("age_s"))),
                    queue="—",
                )
            )
            row.add_css_class("mono")
            self._list.append(row)
            self._rows.append(row)
