"""Assemble the reusable Python client and its bounded transport."""
from .settings import Settings
from .transport import PublicTransport


def create_client(settings=None):
    from .client import NisarClient
    settings = settings or Settings()
    return NisarClient(PublicTransport(settings), settings.cache_dir)
