"""SharpAPI (sharpapi.io): sportsbook and exchange odds, read-only. See :mod:`.client`."""

from .client import BASE_URL, KEY_VAR, SharpAPIClient, SharpAPIError, api_key

__all__ = ["BASE_URL", "KEY_VAR", "SharpAPIClient", "SharpAPIError", "api_key"]
