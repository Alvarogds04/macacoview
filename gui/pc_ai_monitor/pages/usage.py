# pyright: reportAttributeAccessIssue=false
"""Tokens: Pi, Codex CLI and Claude Code, by provider and by model.

Design 07: KPI strip on top; the two CLI stat cards side by side; «POR
PROVEEDOR» full width; «POR MODELO» and «SUSCRIPCIÓN CODEX» side by side.
The block order in ``blocks`` stays the flat reading order.
"""

from gi.repository import Gtk

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.usage import (
    ClaudeCodeKpiBlock,
    CodexBlock,
    CodexCliKpiBlock,
    ModelTokensBlock,
    ProviderDonutBlock,
    TokenKpiBlock,
)


class TokensPage(Dashboard):
    blocks = (
        TokenKpiBlock,
        CodexCliKpiBlock,
        ClaudeCodeKpiBlock,
        ProviderDonutBlock,
        ModelTokensBlock,
        CodexBlock,
    )

    def __init__(self) -> None:
        super().__init__()
        (
            kpi,
            codex_cli,
            claude_code,
            providers,
            models,
            codex,
        ) = self._blocks

        body = self.get_child().get_child()
        while (child := body.get_first_child()) is not None:
            body.remove(child)

        clis = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        clis.set_homogeneous(True)
        clis.append(codex_cli)
        clis.append(claude_code)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom.set_homogeneous(True)
        bottom.append(models)
        bottom.append(codex)

        body.append(kpi)
        body.append(clis)
        body.append(providers)
        body.append(bottom)
