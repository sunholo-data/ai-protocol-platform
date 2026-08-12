"""
Gemini Live voice agent — real-time bidirectional voice via WebSocket.

Uses ADK LiveRunner + LiveRequestQueue for:
  - STT + LLM + TTS in one streaming pipeline
  - Function calling mid-voice-conversation
  - Barge-in (interruption) support

Replaces v5's /api/tts-stream endpoint.
"""

# TODO: Implement create_live_agent() using gemini-2.0-flash-live
# TODO: Implement WebSocket handler for browser audio
