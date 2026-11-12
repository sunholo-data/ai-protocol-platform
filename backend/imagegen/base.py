"""The image-generation engine interface. Narrow on purpose, like ``voice.base``.

An engine may be fast (a cloud API answering in seconds) or slow and shared (a
local GPU that queues jobs behind a lock). Both fit: ``generate`` returns
``status="done"`` with the pictures, or ``status="pending"`` with a ``job``
token once the caller's wait budget is spent; ``resume(job)`` picks it up
again. Engine-specific knobs travel in ``ImageRequest.extras``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Mode = Literal["generate", "edit", "vary"]
Progress = Callable[[str], None]


@dataclass
class GeneratedImage:
    data: bytes
    mime: str
    seed: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class ImageRequest:
    prompt: str
    mode: Mode = "generate"
    # Input pictures for edit / vary, as ``(bytes, mime)``.
    inputs: list[tuple[bytes, str]] = field(default_factory=list)
    count: int = 1
    width: int | None = None
    height: int | None = None
    model: str | None = None
    # How far an edit / variation may move from its input, 0..1 (engine default when None).
    strength: float | None = None
    seed: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageResult:
    status: Literal["done", "pending"]
    images: list[GeneratedImage] = field(default_factory=list)
    # Set when pending: what ``resume`` needs to pick the job up again.
    job: str = ""
    # A human-readable state ("waiting for the GPU", "step 4/9") or note.
    detail: str = ""
    model: str = ""


@runtime_checkable
class ImageProvider(Protocol):
    """An image engine. Implementations live in private packages (or, later, built in)."""

    name: str

    async def generate(self, request: ImageRequest, progress: Progress | None = None) -> ImageResult:
        """Make pictures. Raises ValueError for a request the engine cannot do
        (unknown model, edit without input), RuntimeError when the engine fails."""
        ...

    async def resume(self, job: str, progress: Progress | None = None) -> ImageResult:
        """Continue waiting for a job ``generate`` returned as pending."""
        ...

    def describe(self) -> dict[str, Any]:
        """``{"models": {id: {"label", "modes"}}, "default_model": id, "notes": str}``. Pure; no I/O."""
        ...
