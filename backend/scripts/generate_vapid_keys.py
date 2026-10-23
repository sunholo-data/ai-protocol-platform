"""Print a fresh VAPID key pair for Web Push, as env lines.

    uv run python scripts/generate_vapid_keys.py mailto:ops@example.com

Store the output with the deployment's other secrets. Rotating the keys
invalidates every existing subscription (browsers re-subscribe when the user
turns notifications on again).
"""

from __future__ import annotations

import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> int:
    subject = sys.argv[1] if len(sys.argv) > 1 else ""
    if not subject.startswith(("mailto:", "https://")):
        print("usage: generate_vapid_keys.py mailto:you@example.com", file=sys.stderr)
        return 2
    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    print(f"VAPID_PUBLIC_KEY={_b64url(public_raw)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(private_raw)}")
    print(f"VAPID_SUBJECT={subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
