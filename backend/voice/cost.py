"""Per-provider cost estimates for OTel span attributes.

Numbers are USD per million characters synthesized. Cross-checked against
https://cloud.google.com/text-to-speech/pricing.

These are *estimates* for the analytics span attribute
`voice.cost_estimate_usd`, not invoiced billing. Actual GCP billing
trumps these numbers. Update when tier prices change.
"""

# USD per million characters synthesized.
_TTS_USD_PER_MILLION_CHARS = {
    "gcp_standard": 4.0,
    "gcp_wavenet": 4.0,
    "gcp_neural2": 16.0,
    "gcp_chirp3hd": 30.0,
    "gcp_gemini": 30.0,  # Gemini-TTS, priced with the premium tiers
    "browser": 0.0,  # no cost - local synth
}


def tts_cost_usd(provider_name: str, chars: int) -> float:
    """Estimated USD for synthesizing `chars` characters via `provider_name`.

    Unknown providers return 0.0 (no estimate). They still emit a span; the
    dashboard will surface them as "unknown provider, no cost estimate" so
    we notice and update the table.
    """
    rate = _TTS_USD_PER_MILLION_CHARS.get(provider_name)
    if rate is None:
        return 0.0
    return (chars / 1_000_000.0) * rate
