"""Browser TTS provider - signal-only.

The browser path doesn't synthesize on the backend; the frontend calls
`window.speechSynthesis` locally. This provider exists so the registry
can return *something* for `name="browser"`, and the voice route can
detect it and signal the frontend ("you handle it locally") rather
than serving an audio blob.

The `.synthesize()` method raises NotImplementedError on purpose. The
route layer checks `provider.name == "browser"` and returns a JSON
`{"provider": "browser"}` response instead of calling synthesize.
"""

from voice.base import VoiceCapabilities


class BrowserTTSProvider:
    """Marker provider that delegates synthesis to the browser's Web Speech API."""

    name = "browser"

    async def synthesize(
        self,
        text: str,
        lang: str,
        voice: str | None,
        extras: dict | None,
    ) -> tuple[bytes, str]:
        # The voice route should never call this - it checks `provider.name`
        # first and returns a "use the browser" signal to the FE. If it does
        # get called, something in the route layer regressed.
        raise NotImplementedError(
            "BrowserTTSProvider.synthesize must be handled at the route layer; "
            "the browser does the work locally via window.speechSynthesis. "
            "Check that voice_routes.py routes browser-provider requests "
            "to the JSON signal path."
        )

    def describe(self) -> VoiceCapabilities:
        # The frontend will use Web Speech regardless of what we list here;
        # languages reflects "we expect the browser to handle this lang well",
        # not a hard guarantee. Empty list = no claims; the frontend trusts
        # window.speechSynthesis to enumerate locally.
        return VoiceCapabilities(
            tts=True,
            stt=False,
            streaming=False,
            languages=[],
        )
