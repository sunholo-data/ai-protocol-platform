"""Image generation behind swappable engines: the image twin of ``voice``.

Public surface:
    ``ImageProvider`` - Protocol an engine implements.
    ``ImageRequest`` / ``ImageResult`` / ``GeneratedImage`` - what goes in and out.
    ``get_image_provider(name=None)`` - resolve an engine from name > env > none.
    ``available_image_providers()`` - the names this deployment offers.

No engine is built in yet. A deployment adds its own through a private
package's ``imagegen.py`` (``IMAGE_PROVIDERS``, via ``extensions``), e.g. a
host-local model; a cloud engine (Gemini image, Imagen) becomes one more
branch in ``registry._build`` without touching the tool.
"""

from imagegen.base import GeneratedImage, ImageProvider, ImageRequest, ImageResult
from imagegen.registry import available_image_providers, get_image_provider

__all__ = [
    "GeneratedImage",
    "ImageProvider",
    "ImageRequest",
    "ImageResult",
    "available_image_providers",
    "get_image_provider",
]
