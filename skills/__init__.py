"""Skills system for the Discord bot.

Provides markdown-based skills compatible with the Agent Skills spec
(Claude Code / Cursor / Gemini CLI compatible).
"""

from . import parser
from . import registry
from . import triggers

__all__ = ["parser", "registry", "triggers"]
