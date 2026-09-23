"""Tokens: Pi, Codex CLI and Claude Code, by provider and by model."""

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
