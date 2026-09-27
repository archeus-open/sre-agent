"""Context package: budgeted, sectioned prompt assembly."""

from .manager import ContextManager, Section, estimate_tokens

__all__ = ["ContextManager", "Section", "estimate_tokens"]
