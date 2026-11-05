# PyGObject no trae stubs propios; ver gui/typings/gui y gui/pyrightconfig.json.
# pyright: reportAttributeAccessIssue=false
"""Tests del restyle Recursos + Tokens (T6, diseño 07, S10/S12).

Las reglas de datos nuevas viven puras en ``recursos_vista`` y
``tokens_vista`` (sin GTK); las de construcción y clic usan widgets reales y
necesitan display — aunque sea virtual:

    xvfb-run -a python3 -m unittest tests.test_recursos_tokens -v

Nunca se toca la red ni el administrador: ``_aislamiento.isolate()`` al
importar, y los snapshots se arman acá con valores de INVENTARIO-APP.md.
"""

import unittest

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

_aislamiento.isolate()

from pc_ai_monitor.datos import GROUP_KEYS, Sample, Snapshot
from pc_ai_monitor.formato import gib_label


def make_snapshot(hidden=(), with_history=True):
    """Snapshot con los valores de la simulación (INVENTARIO-APP.md)."""
    stats = {
        "memory": {
            "total_gib": 122.0,
            "used_gib": 70.9,
            "available_gib": 51.1,
            "swap_total_gib": 8.0,
            "swap_used_gib": 2.3,
        },
        "models": [
            {
                "alias": "Gufo",
                "pid": 1001,
                "port": 11070,
                "model": "qwen3.8-flash.gguf",
                "rss_gib": 49.2,
                "cpu": 3.1,
                "gtt_gib": 85.0,
                "vram_gib": 0.0,
            },
            {
                "alias": "Abito",
                "pid": 1002,
                "port": 11002,
                "model": "abito.gguf",
                "rss_gib": 9.0,
                "cpu": 0.4,
                "gtt_gib": 20.0,
                "vram_gib": 0.0,
            },
        ],
        "groups": {
            "pi": {"rss_gib": 6.8, "cpu": 10.0, "pids": [2001, 2002]},
            "hermes": {"rss_gib": 2.1, "cpu": 1.0, "pids": [3001]},
            "firefox": {"rss_gib": 4.3, "cpu": 5.0, "pids": [4001]},
            "system": {"rss_gib": 3.9, "cpu": 1.0, "pids": [1]},
            "other": {"rss_gib": 4.6, "cpu": 2.0, "pids": [5001]},
        },
        "processes": [
            {
                "pid": 2001,
                "comm": "node (pi)",
                "rss_kb": 1_900_000,
                "cpu": 6.0,
                "args": "node .",
            },
            {
                "pid": 2002,
                "comm": "pi-helper",
                "rss_kb": 300_000,
                "cpu": 0.5,
                "args": "pi --serve",
            },
            {
                "pid": 4001,
                "comm": "firefox",
                "rss_kb": 2_800_000,
                "cpu": 4.0,
                "args": "firefox",
            },
            {
                "pid": 3001,
                "comm": "hermes",
                "rss_kb": 1_600_000,
                "cpu": 1.0,
                "args": "hermes",
            },
            {
                "pid": 5001,
                "comm": "gnome-shell",
                "rss_kb": 1_100_000,
                "cpu": 2.0,
                "args": "gnome-shell",
            },
            {
                "pid": 1,
                "comm": "systemd",
                "rss_kb": 50_000,
                "cpu": 0.1,
                "args": "systemd",
            },
        ],
    }
    tokens = {
        "pi": {
            "status": "ok",
            "total": 18_400_000,
            "turns": 1284,
            "cost_usd": 41.27,
            "cache_read": 14_900_000,
            "models": [
                {
                    "name": "gufo",
                    "provider": "llamacpp",
                    "total": 11_400_000,
                    "turns": 900,
                },
                {
                    "name": "gpt-5.6-sol",
                    "provider": "openai-codex",
                    "total": 4_400_000,
                    "turns": 200,
                },
                {
                    "name": "claude-x",
                    "provider": "anthropic",
                    "total": 2_600_000,
                    "turns": 120,
                },
                {
                    "name": "abito-gpt",
                    "provider": "abito-direct",
                    "total": 0,
                    "turns": 41,
                },
            ],
        },
        "codex": {
            "status": "ok",
            "plan": "prolite",
            "windows": [
                {"label": "5 h", "used_percent": 38.0, "resets_at": "17:42"},
                {"label": "semana", "used_percent": 61.0, "resets_at": "lun 09:00"},
            ],
        },
        "codex_cli": {
            "status": "ok",
            "total": 9_300_000,
            "reasoning": 2_200_000,
            "cache_write": 700_000,
            "cache_read": 6_800_000,
            "turns": 311,
            "cost_usd": 12.40,
            "models": [],
        },
        "claude_code": {
            "status": "ok",
            "total": 12_100_000,
            "reasoning": 1_400_000,
            "cache_write": 1_900_000,
            "cache_read": 8_100_000,
            "turns": 202,
            "cost_usd": 18.35,
            "models": [],
        },
    }
    history = ()
    if with_history:
        history = tuple(
            Sample(
                used_gib=70.0 + step,
                available_gib=52.0 - step,
                groups=(6.8 + step, 2.1, 4.3, 3.9, 4.6),
            )
            for step in range(5)
        )
    return Snapshot(
        stats=stats, tokens=tokens, history=history, hidden_groups=tuple(hidden)
    )


# ---------------------------------------------------------------------------
# Reglas puras nuevas (recursos_vista / tokens_vista)
# ---------------------------------------------------------------------------


class RecursosVistaTests(unittest.TestCase):
    """Reglas puras del restyle de Recursos."""

    def test_treemap_slices_sorted_desc_without_hidden_or_zero(self):
        from pc_ai_monitor.blocks.recursos_vista import treemap_slices

        values = dict(zip(GROUP_KEYS, (6.8, 2.1, 4.3, 3.9, 4.6)))
        slices = treemap_slices(
            values,
            hidden={"hermes"},
            colour_for=lambda _key: "#000000",
            label_for={
                "pi": "🥧 Pi",
                "hermes": "🪽 Hermes",
                "firefox": "🦊 Firefox",
                "system": "⚙️ Sistema (root)",
                "other": "📦 Otros",
            }.__getitem__,
        )

        self.assertEqual(
            [s.label for s in slices],
            ["🥧 Pi", "📦 Otros", "🦊 Firefox", "⚙️ Sistema (root)"],
        )
        self.assertTrue(all(s.value > 0 for s in slices))

    def test_treemap_drops_zero_groups(self):
        from pc_ai_monitor.blocks.recursos_vista import treemap_slices

        values = dict.fromkeys(GROUP_KEYS, 0.0)
        values["pi"] = 6.8
        slices = treemap_slices(
            values,
            hidden=set(),
            colour_for=lambda _k: "#fff",
            label_for=lambda key: {"pi": "🥧 Pi"}.get(key, key),
        )
        self.assertEqual([s.label for s in slices], ["🥧 Pi"])

    def test_visible_total_matches_gib_label_pinned_format(self):
        """El total visible usa el formato que test_core fija (gib_label(x, 2))."""
        from pc_ai_monitor.blocks.recursos_vista import visible_total

        values = dict(zip(GROUP_KEYS, (2.5, 9.0, 1.0, 12.0, 5.0)))
        total = visible_total(values, hidden={"hermes"})
        self.assertEqual(gib_label(total, 2), gib_label(2.5 + 1.0 + 12.0 + 5.0, 2))

    def test_top_processes_orders_by_rss_and_caps_at_ten(self):
        from pc_ai_monitor.blocks.recursos_vista import top_processes

        processes = [
            {"pid": index, "comm": f"p{index}", "rss_kb": index * 100, "cpu": 1.0}
            for index in range(15)
        ]
        found = top_processes(processes, pids={index for index in range(15)})
        self.assertEqual(len(found), 10)
        self.assertEqual(found[0]["pid"], 14)
        self.assertEqual(found[-1]["pid"], 5)

    def test_top_processes_only_group_pids(self):
        from pc_ai_monitor.blocks.recursos_vista import top_processes

        processes = [
            {"pid": 1, "comm": "node", "rss_kb": 900_000, "cpu": 1.0},
            {"pid": 2, "comm": "other", "rss_kb": 9_000_000, "cpu": 1.0},
        ]
        found = top_processes(processes, pids={1})
        self.assertEqual([p["pid"] for p in found], [1])


class TokensVistaTests(unittest.TestCase):
    """Reglas puras del restyle de Tokens."""

    def test_provider_totals_groups_by_provider(self):
        from pc_ai_monitor.blocks.tokens_vista import provider_totals

        models = [
            {"provider": "llamacpp", "total": 100},
            {"provider": "llamacpp", "total": 50},
            {"provider": "anthropic", "total": 30},
            {"total": 20},
        ]
        totals = provider_totals(models)
        self.assertEqual(totals["llamacpp"], 150.0)
        self.assertEqual(totals["anthropic"], 30.0)
        self.assertEqual(totals["sin proveedor"], 20.0)

    def test_provider_slices_top_eight_plus_resto(self):
        from pc_ai_monitor.blocks.tokens_vista import provider_slices

        totals = {f"prov{index}": float(100 - index) for index in range(11)}
        slices = provider_slices(totals, colour_for=lambda _n, _i: "#abc")

        labels = [s.label for s in slices]
        self.assertEqual(len(slices), 9)
        self.assertEqual(labels[:2], ["prov0", "prov1"])
        self.assertEqual(labels[-1], "resto (3)")
        self.assertEqual(slices[-1].value, 100 - 8 + 100 - 9 + 100 - 10)

    def test_provider_slices_without_resto(self):
        from pc_ai_monitor.blocks.tokens_vista import provider_slices

        totals = {"a": 10.0, "b": 5.0}
        slices = provider_slices(totals, colour_for=lambda _n, _i: "#abc")
        self.assertEqual([s.label for s in slices], ["a", "b"])

    def test_uncounted_note_lists_models_with_turns(self):
        from pc_ai_monitor.blocks.tokens_vista import uncounted_note

        models = [
            {"name": "abito-gpt", "total": 0, "turns": 41},
            {"name": "gufo", "total": 10, "turns": 3},
        ]
        note = uncounted_note(models)
        self.assertEqual(
            note, "Sin conteo de tokens (Pi no los recibe): abito-gpt (41 turnos)."
        )

    def test_uncounted_note_empty_when_all_counted(self):
        from pc_ai_monitor.blocks.tokens_vista import uncounted_note

        self.assertEqual(uncounted_note([{"name": "x", "total": 5, "turns": 1}]), "")

    def test_window_colour_thresholds(self):
        """69 verde, 70 ámbar, 89 ámbar, 90 rojo (INVENTARIO: ≥70 / ≥90)."""
        from pc_ai_monitor.blocks.tokens_vista import window_colour

        palette = {"error": "#e", "warning": "#w", "green": "#g"}
        self.assertEqual(window_colour(69, **palette), "#g")
        self.assertEqual(window_colour(70, **palette), "#w")
        self.assertEqual(window_colour(89, **palette), "#w")
        self.assertEqual(window_colour(90, **palette), "#e")

    def test_codex_note_names_plan_and_reset(self):
        from pc_ai_monitor.blocks.tokens_vista import codex_note

        note = codex_note("prolite", [{"resets_at": "17:42"}])
        self.assertEqual(note, "Plan prolite · resetea 17:42")

    def test_codex_note_without_windows(self):
        from pc_ai_monitor.blocks.tokens_vista import codex_note

        self.assertEqual(
            codex_note("prolite", []), "Sin ventanas informadas por codexbar."
        )


# ---------------------------------------------------------------------------
# Comportamiento conservado, a través de la interfaz pública de los widgets
# ---------------------------------------------------------------------------


def gtk_case(cls):
    """Decorador: la clase necesita display; salta sin Gtk disponible."""
    return cls


class WidgetCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi

            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk

            if not Gtk.init_check():
                raise unittest.SkipTest("sin display (usá xvfb-run)")
        except ImportError as error:  # pragma: no cover
            raise unittest.SkipTest(f"PyGObject no disponible: {error}") from error


class GroupsBlockTests(WidgetCase):
    """Grupos: treemap + leyenda clicable con el detalle en línea."""

    def _block(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        block = GroupsBlock()
        block.update_snapshot(make_snapshot())
        return block

    def test_legend_row_click_opens_process_list(self):
        block = self._block()
        block._on_row_pressed(None, 1, 0, 0, "pi")

        self.assertTrue(block._revealer.get_reveal_child())
        heading = block._detail.get_first_child()
        self.assertEqual(heading.get_text(), "PROCESOS DE 🥧 PI")
        rows = []
        child = heading.get_next_sibling()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        # pi tiene pids 2001 y 2002: el más gordo primero.
        self.assertEqual(len(rows), 2)

    def test_legend_row_click_again_closes(self):
        block = self._block()
        block._on_row_pressed(None, 1, 0, 0, "pi")
        self.assertTrue(block._revealer.get_reveal_child())
        block._on_row_pressed(None, 1, 0, 0, "pi")
        self.assertFalse(block._revealer.get_reveal_child())

    def test_empty_group_message(self):
        snapshot = make_snapshot()
        assert snapshot.stats is not None
        snapshot.stats["groups"]["other"] = {"rss_gib": 4.6, "cpu": 2.0, "pids": []}
        block = self._block()
        block.update_snapshot(snapshot)
        block._on_row_pressed(None, 1, 0, 0, "other")
        heading = block._detail.get_first_child()
        note = heading.get_next_sibling()
        self.assertEqual(note.get_text(), "Este grupo no tiene procesos ahora mismo.")

    def test_hidden_group_disappears(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        block = GroupsBlock()
        block.update_snapshot(make_snapshot(hidden=("hermes",)))

        self.assertFalse(block._rows["hermes"][0].get_visible())
        self.assertTrue(block._rows["pi"][0].get_visible())
        labels = [slice_.label for slice_ in block._donut._slices]
        self.assertNotIn("🪽 Hermes", labels)
        self.assertIn("🥧 Pi", labels)

    def test_total_excludes_hidden(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        block = GroupsBlock()
        block.update_snapshot(make_snapshot(hidden=("hermes",)))
        visible = 6.8 + 4.3 + 3.9 + 4.6
        self.assertEqual(block._donut._center_value, gib_label(visible, 2))

    def test_treemap_tile_click_toggles_group(self):
        block = self._block()
        slice_ = next(s for s in block._donut._slices if s.label == "🥧 Pi")
        block._on_tile_selected(slice_)
        self.assertTrue(block._revealer.get_reveal_child())
        block._on_tile_selected(slice_)
        self.assertFalse(block._revealer.get_reveal_child())


class ModelsBlockTests(WidgetCase):
    """Modelos: acordeón con GTT/RSS/CPU y la línea de PID/puerto."""

    def _block(self):
        from pc_ai_monitor.blocks.modelos import ModelsBlock

        block = ModelsBlock()
        block.update_snapshot(make_snapshot())
        return block

    def test_click_opens_detail_in_place(self):
        block = self._block()
        card = block._cards["Gufo"]
        card._toggle()
        self.assertTrue(card._revealer.get_reveal_child())
        self.assertEqual(
            card._facts.get_text(),
            "qwen3.8-flash.gguf · PID 1001 · puerto 11070",
        )

    def test_opening_one_closes_the_other(self):
        block = self._block()
        gufo = block._cards["Gufo"]
        abito = block._cards["Abito"]
        gufo._toggle()
        abito._toggle()
        self.assertFalse(gufo._revealer.get_reveal_child())
        self.assertTrue(abito._revealer.get_reveal_child())

    def test_empty_message(self):
        from pc_ai_monitor.blocks.modelos import ModelsBlock

        block = ModelsBlock()
        block.update_snapshot(make_snapshot())
        block.update_snapshot(
            Snapshot(stats={"models": [], "groups": {}, "processes": []})
        )
        self.assertIn(block._hint.get_text(), {"Sin modelos locales cargados."})


class HistoryBlockTests(WidgetCase):
    """Historial: filas por serie, nota de ventana, ocultos fuera."""

    def _block(self, hidden=()):
        from pc_ai_monitor.blocks.historial import HistoryBlock

        block = HistoryBlock()
        block.update_snapshot(make_snapshot(hidden=hidden))
        return block

    def test_note_counts_samples_and_last_value(self):
        block = self._block()
        self.assertEqual(block._note.get_text(), "Ventana: 5 muestras a 1 Hz")
        self.assertEqual(block._values["pi"].get_text(), gib_label(10.8, 1))

    def test_accumulating_note_without_history(self):
        block = self._block()
        block.update_snapshot(
            Snapshot(stats={"groups": {}, "processes": []}, history=())
        )
        self.assertEqual(block._note.get_text(), "Acumulando historial…")

    def test_hidden_group_rows_hide_but_keep_accumulating(self):
        block = self._block(hidden=("pi", "hermes"))
        self.assertFalse(block._rows["pi"].get_visible())
        self.assertTrue(block._rows["firefox"].get_visible())
        self.assertEqual(block._sparks["pi"]._values[-1], 10.8)
        self.assertEqual(block._sparks["firefox"]._values[-1], 4.3)


class ProviderDonutTests(WidgetCase):
    """«POR PROVEEDOR»: donut clicable filtra la leyenda; clic de nuevo borra."""

    def _block(self):
        from pc_ai_monitor.blocks.usage import ProviderDonutBlock

        block = ProviderDonutBlock()
        block.update_snapshot(make_snapshot())
        return block

    def test_resto_slice_appears_only_when_needed(self):
        block = self._block()
        labels = [s.label for s in block._donut._slices]
        self.assertNotIn("resto (0)", labels)
        self.assertEqual(labels[0], "llamacpp")

    def test_click_filters_legend_and_click_again_clears(self):
        block = self._block()
        slice_ = next(s for s in block._donut._slices if s.label == "llamacpp")

        block._focus(slice_)
        rows = list(block._legend)
        texts = [row.get_first_child().get_text() for row in rows]
        self.assertEqual(texts, ["●", "●"])  # fila llamacpp + su modelo gufo
        self.assertIn("Filtrado por llamacpp", block._note.get_text())

        block._focus(slice_)
        rows = list(block._legend)
        self.assertEqual(len(rows), 4)  # cuatro proveedores, sin filtro
        self.assertEqual(
            block._note.get_text(), "Hacé clic en un proveedor para ver sus modelos."
        )

    def test_focused_provider_shows_its_models(self):
        block = self._block()
        slice_ = next(s for s in block._donut._slices if s.label == "llamacpp")
        block._focus(slice_)
        sub = list(block._legend)[1]
        # fila sub: swatch, nombre, valor
        self.assertEqual(sub.get_first_child().get_next_sibling().get_text(), "gufo")


class CodexBlockTests(WidgetCase):
    """«SUSCRIPCIÓN CODEX»: ventanas con color por presión y nota de plan."""

    def _block(self):
        from pc_ai_monitor.blocks.usage import CodexBlock

        block = CodexBlock()
        block.update_snapshot(make_snapshot())
        return block

    def test_windows_rendered_with_labels(self):
        block = self._block()
        children = []
        child = block._row.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        self.assertEqual(len(children), 2)
        self.assertEqual(block._note.get_text(), "Plan prolite · resetea 17:42")

    def test_empty_windows_message(self):
        from pc_ai_monitor.blocks.usage import CodexBlock

        block = CodexBlock()
        snapshot = make_snapshot()
        assert snapshot.tokens is not None
        snapshot.tokens["codex"] = {"status": "ok", "plan": "prolite", "windows": []}
        block.update_snapshot(snapshot)
        self.assertEqual(
            block._note.get_text(), "Sin ventanas informadas por codexbar."
        )

    def test_widget_uses_shared_thresholds(self):
        """El color del bloque pasa por tokens_vista (69/70/90)."""
        from pc_ai_monitor import theme
        from pc_ai_monitor.blocks import tokens_vista

        block = self._block()
        palette = theme.active()
        self.assertEqual(
            block._pressure(70),
            tokens_vista.window_colour(
                70, error=palette.error, warning=palette.warning, green=palette.green
            ),
        )


class CliStatCardsTests(WidgetCase):
    """CODEX CLI / CLAUDE CODE: misma interfaz _tiles que fija test_core."""

    def test_cli_tiles_keep_pinned_interface(self):
        from pc_ai_monitor.blocks.usage import ClaudeCodeKpiBlock, CodexCliKpiBlock

        codex = CodexCliKpiBlock()
        codex.update_snapshot(make_snapshot())
        self.assertEqual(codex._tiles["total"]._value.get_text(), "9.3M")
        self.assertEqual(codex._tiles["reasoning"]._value.get_text(), "2.2M")
        self.assertEqual(codex._tiles["cache_write"]._value.get_text(), "700.0k")
        self.assertEqual(codex._tiles["turns"]._value.get_text(), "311")
        self.assertEqual(codex._tiles["cost"]._value.get_text(), "$12.40")

        claude = ClaudeCodeKpiBlock()
        claude.update_snapshot(make_snapshot())
        self.assertEqual(claude._tiles["total"]._value.get_text(), "12.1M")
        self.assertEqual(claude._tiles["cache_read"]._value.get_text(), "8.1M")


class PageLayoutTests(WidgetCase):
    """Las páginas enteras arman y aceptan un snapshot."""

    def test_recursos_page_two_columns(self):
        from pc_ai_monitor.pages.recursos import RecursosPage

        page = RecursosPage()
        page.update_snapshot(make_snapshot())
        page.redraw()
        self.assertEqual(len(page._blocks), 5)

    def test_tokens_page_builds_and_updates(self):
        from pc_ai_monitor.pages.usage import TokensPage

        page = TokensPage()
        page.update_snapshot(make_snapshot())
        page.redraw()
        self.assertEqual(len(page._blocks), 6)


if __name__ == "__main__":
    unittest.main()


class MeterColourTests(unittest.TestCase):
    """Cada tipo de medidor tiene su color: SWAP azul, LIBRE verde (S10)."""

    def test_every_meter_kind_has_a_css_rule(self):
        from pc_ai_monitor import theme
        from pc_ai_monitor.widgets.barras import KINDS

        for kind in KINDS:
            if kind == "accent":
                continue
            selector = f".bar-{kind}"
            self.assertIn(selector, theme.BASE_CSS, kind)
            rule = theme.BASE_CSS.split(selector, 1)[1].split("}", 1)[0]
            self.assertIn(f"@theme_{kind}", rule)


class TreemapHeaderTests(unittest.TestCase):
    """La cabecera «RSS total» no se pisa con las fichas."""

    def test_header_leaves_room_for_the_caption(self):
        from pc_ai_monitor.blocks.recursos_vista import GroupTreemap

        # 10 pt de texto en negrita necesitan ~16 px más margen.
        self.assertGreaterEqual(GroupTreemap.HEADER, 22)

    def test_caption_is_drawn_above_the_tiles(self):
        """El texto parte del borde superior y termina antes que las fichas."""
        from unittest import mock

        from pc_ai_monitor.blocks import recursos_vista
        from pc_ai_monitor.widgets.base import Slice

        treemap = recursos_vista.GroupTreemap()
        treemap.set_data([Slice("Pi", 6.8, "#8FB8DD")], "6.80G", "RSS total")
        calls = []
        with mock.patch.object(recursos_vista, "draw_text", lambda *a, **k: calls.append(a)):
            import cairo

            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 190)
            treemap._draw(treemap, cairo.Context(surface), 400, 190)
        caption = next(c for c in calls if "RSS total" in c[2])
        top_of_text, font = caption[4], caption[6]
        self.assertLessEqual(top_of_text + font * 1.6, recursos_vista.GroupTreemap.HEADER)
