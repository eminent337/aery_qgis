"""AI Proxy Worker for Aery QGIS Plugin.

Manages provider keys, enforces rate limits, and falls back across providers.
"""

from .worker import AIProxyWorker, get_proxy_worker

__all__ = ["AIProxyWorker", "get_proxy_worker"]