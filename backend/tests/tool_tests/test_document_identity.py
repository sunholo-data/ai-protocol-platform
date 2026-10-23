"""Document identity guard (PRECUT-HARDENING M1b).

The live case is `test_the_live_wrong_document_case`. Everything else exists to
stop the guard being either too strict (blocking legitimate partial names, which
would make the tools unusable) or too loose (the bug it replaces).
"""

from __future__ import annotations

from tools.document_identity import identity_mismatch


class TestTheLiveBug:
    def test_the_live_wrong_document_case(self):
        """2026-08-09: 'Brightfield — Iberia PPA' resolved to a different contract."""
        msg = identity_mismatch("Brightfield — Iberia PPA", "2024_Northwind_ACME_Iberia.pdf")
        assert msg is not None
        assert "brightfield" in msg
        # It must name the actual file so the model can correct itself.
        assert "2024_Northwind_ACME_Iberia.pdf" in msg
        # And it must forbid the specific thing that happened.
        assert "Do NOT present" in msg

    def test_one_shared_geography_word_is_not_identity(self):
        """'iberia' appears in both — that alone must not confirm the match."""
        assert identity_mismatch("Brightfield Spain", "Northwind_ACME_Iberia.pdf") is not None


class TestLegitimateNamesStillPass:
    def test_partial_user_name_matches_the_real_file(self):
        assert identity_mismatch("Brightfield PPA", "Brightfield PPA model Iberia.pdf") is None

    def test_shorthand_matches_a_longer_formal_filename(self):
        assert identity_mismatch("Vantage Grid PPA", "Vantage Grid Power Purchase Agreement.pdf") is None

    def test_separators_and_case_do_not_matter(self):
        assert identity_mismatch("northwind acme", "2024_Northwind_ACME_Iberia.pdf") is None

    def test_substring_within_a_joined_token_matches(self):
        """'north' must match inside 'Northwind' — real filenames run words together."""
        assert identity_mismatch("north iberia", "2024_Northwind_ACME_Iberia.pdf") is None


class TestOptOutPaths:
    def test_blank_expected_name_is_not_a_mismatch(self):
        """Opt-in by design: a required param would make the model invent a value."""
        for blank in (None, "", "   "):
            assert identity_mismatch(blank, "Anything.pdf") is None

    def test_all_noise_name_cannot_confirm_or_deny(self):
        """'the contract' identifies nothing, so it must not be read as confirmation."""
        assert identity_mismatch("the contract", "2024_Northwind_ACME_Iberia.pdf") is None

    def test_unknown_actual_filename_does_not_fabricate_a_mismatch(self):
        assert identity_mismatch("Brightfield", None) is None
        assert identity_mismatch("Brightfield", "") is None


class TestNoiseWords:
    def test_noise_words_alone_never_satisfy_identity(self):
        """Every PPA is a 'PPA' — matching on that would let any contract pass as any other."""
        msg = identity_mismatch("Brightfield PPA agreement", "Some Other PPA agreement.pdf")
        assert msg is not None

    def test_extension_is_not_part_of_the_name(self):
        assert identity_mismatch("Brightfield pdf", "Brightfield.pdf") is None
