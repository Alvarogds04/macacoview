"""Tokens: what Pi spent, by provider and by model."""

from pc_ai_monitor.blocks import Dashboard
from pc_ai_monitor.blocks.usage import (
    CodexBlock,
    ModelTokensBlock,
    ProviderDonutBlock,
    TokenKpiBlock,
)


class TokensPage(Dashboard):
    blocks = (TokenKpiBlock, ProviderDonutBlock, ModelTokensBlock, CodexBlock)
