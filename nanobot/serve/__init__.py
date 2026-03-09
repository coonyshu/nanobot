"""
nanobot serve — FastAPI Web/Voice service.

Usage:
    from nanobot.serve.app import create_app
    app = create_app()
"""

from .app import create_app

__all__ = ["create_app"]
