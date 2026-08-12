"""Unit tests for `channels._chunk.chunk_message`.

The helper splits long replies into <=max_length Discord-compatible
chunks. Word-boundary splits are preferred so the user never sees a
single word cut in half. Code blocks and oversized single tokens
fall back to hard-split because there's no boundary to find.

Tests cover the boundary cases that the design called out:
    - short text → one chunk
    - exact-fit text → one chunk
    - boundary text → splits on whitespace
    - oversized single word → hard split
    - empty/whitespace input → empty list
"""

from __future__ import annotations

import pytest

from channels._chunk import chunk_message


class TestChunkMessage:
    """`chunk_message` returns chunks each <= max_length, never breaking words when possible."""

    def test_short_text_returns_single_chunk(self) -> None:
        chunks = chunk_message("hello world", max_length=2000)
        assert chunks == ["hello world"]

    def test_empty_input_returns_empty_list(self) -> None:
        assert chunk_message("", max_length=2000) == []
        assert chunk_message("   ", max_length=2000) == []

    def test_exact_fit_returns_single_chunk(self) -> None:
        text = "a" * 2000
        chunks = chunk_message(text, max_length=2000)
        assert chunks == [text]

    def test_splits_at_word_boundary_when_oversized(self) -> None:
        # Build text whose natural split point lies inside max_length: a series
        # of 50-char words separated by spaces, max_length=120 → two chunks,
        # neither word mid-broken.
        words = [f"{i:02d}" + "x" * 48 for i in range(6)]  # 6 words of 50 chars each
        text = " ".join(words)
        chunks = chunk_message(text, max_length=120)
        # Each chunk must be <= max_length
        for chunk in chunks:
            assert len(chunk) <= 120
        # Reassembling whitespace-tolerant should preserve the words
        reassembled = " ".join(chunks).split()
        assert reassembled == words

    def test_never_breaks_word_mid_token_when_a_boundary_exists(self) -> None:
        text = "alpha beta gamma " * 100  # plenty of word boundaries
        chunks = chunk_message(text, max_length=50)
        # Every chunk must either be empty or end at a word boundary —
        # specifically: no chunk should end mid-word (i.e., last char is
        # alphanumeric AND next chunk's first char is also alphanumeric
        # AND they would form a continued word). Easier sanity check:
        # joining chunks with " " back to text gives the same set of words.
        words_out = " ".join(chunks).split()
        words_in = text.split()
        assert words_out == words_in

    def test_oversized_single_word_is_hard_split(self) -> None:
        # A single token longer than max_length has no boundary to split on;
        # the chunker MUST still produce chunks of <= max_length.
        text = "X" * 5000
        chunks = chunk_message(text, max_length=2000)
        assert len(chunks) == 3
        assert len(chunks[0]) == 2000
        assert len(chunks[1]) == 2000
        assert len(chunks[2]) == 1000
        assert "".join(chunks) == text

    def test_invalid_max_length_raises(self) -> None:
        with pytest.raises(ValueError):
            chunk_message("hello", max_length=0)
        with pytest.raises(ValueError):
            chunk_message("hello", max_length=-5)

    def test_chunks_strip_leading_whitespace(self) -> None:
        # Word-boundary split leaves a leading space on the next chunk;
        # we strip so users don't see ' continued' in the next message.
        text = "abcd " + ("z" * 30) + " " + ("y" * 30)
        chunks = chunk_message(text, max_length=35)
        for chunk in chunks[1:]:
            assert not chunk.startswith(" ")
