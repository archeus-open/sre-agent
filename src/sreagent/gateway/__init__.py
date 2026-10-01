"""API gateway: mock API-key auth, role-based authorization, per-key
token-bucket rate limiting, and round-robin load balancing over upstream
model workers. Build with :func:`create_app`.
"""

from .app import create_app

__all__ = ["create_app"]
