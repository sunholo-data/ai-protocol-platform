"""Image engine registry: name > ``IMAGE_GEN_PROVIDER`` > none.

Explicit string dispatch like ``voice.registry``. Built-in cloud engines go in
``_build``; a deployment's own engines come from private packages
(``imagegen.py`` exposing ``IMAGE_PROVIDERS``, via ``extensions``).
"""

from __future__ import annotations

import os

from imagegen.base import ImageProvider

_BUILT_IN: tuple[str, ...] = ()  # e.g. "gemini_image" when a cloud engine lands


def available_image_providers() -> list[str]:
    from extensions import private_image_provider_names

    return [*_BUILT_IN, *private_image_provider_names()]


def default_image_provider() -> str:
    return (os.getenv("IMAGE_GEN_PROVIDER") or "").strip() or next(iter(available_image_providers()), "")


def get_image_provider(name: str | None = None) -> ImageProvider:
    """Resolve an engine. Raises ValueError when none is configured or the name is unknown."""
    chosen = (name or "").strip() or default_image_provider()
    if not chosen:
        raise ValueError("No image engine is set up on this deployment.")
    return _build(chosen)


def _build(name: str) -> ImageProvider:
    from extensions import private_image_provider

    provider = private_image_provider(name)
    if provider is not None:
        return provider
    known = ", ".join(available_image_providers()) or "none"
    raise ValueError(f"Unknown image engine {name!r}. Available: {known}.")
