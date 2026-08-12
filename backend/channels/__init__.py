"""Channel framework — `BaseChannel` + adapters.

Import `BaseChannel`, `InboundMessage`, `OutboundMessage`, `Attachment`,
`ChannelRegistry`, and the exception types from here. Concrete adapters
(Discord, Email, Telegram, WhatsApp) live in sibling modules and are
registered via `ChannelRegistry.register(...)`.
"""

from channels.attachments import (
    Attachment,
    AttachmentPipeline,
    AttachmentTooLargeError,
    AttachmentUnsupportedTypeError,
)
from channels.base import BaseChannel, InboundMessage, OutboundMessage
from channels.commands import Command, CommandParser
from channels.identity import IdentityResolver
from channels.registry import ChannelRegistry, ChannelRegistryError

__all__ = [
    "Attachment",
    "AttachmentPipeline",
    "AttachmentTooLargeError",
    "AttachmentUnsupportedTypeError",
    "BaseChannel",
    "ChannelRegistry",
    "ChannelRegistryError",
    "Command",
    "CommandParser",
    "IdentityResolver",
    "InboundMessage",
    "OutboundMessage",
]
