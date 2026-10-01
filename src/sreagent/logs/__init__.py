"""Synthetic fleet logs for the demo (see LogStore). Timestamps are
anchored at construction; the sick host replays the SEV2-1042 story.
"""

from .store import LogLine, LogStore

__all__ = ["LogLine", "LogStore"]
