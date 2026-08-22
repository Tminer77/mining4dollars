"""Glossary interpreter: closed vocabulary, greedy binding, no guessing."""

from __future__ import annotations

import datetime as dt

import pytest

from m4d.domain.errors import ValidationError
from m4d.domain.glossary import CORE_GLOSSARY, GlossaryTerm, NewTerm, interpret, normalise_key

NOW = dt.datetime(2026, 8, 22, 8, 50, tzinfo=dt.UTC)


def terms() -> tuple[GlossaryTerm, ...]:
    return tuple(term.materialise(now=NOW) for term in CORE_GLOSSARY)


class TestNormalise:
    def test_collapses_punctuation_and_case(self) -> None:
        assert normalise_key("Linear Time") == "linear-time"
        assert normalise_key("linear_time") == "linear-time"
        assert normalise_key("  LTP  ") == "ltp"


class TestNewTerm:
    def test_rejects_a_blank_definition(self) -> None:
        with pytest.raises(ValidationError, match="definition"):
            NewTerm(slug="tick", name="Tick", definition="  ")

    def test_rejects_a_non_kebab_slug(self) -> None:
        with pytest.raises(ValidationError, match="kebab"):
            NewTerm(slug="1tick", name="Tick", definition="a tick")

    def test_rejects_an_alias_that_duplicates_the_slug(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            NewTerm(slug="tick", name="Tick", definition="a tick", aliases=("tick",))

    def test_deduplicates_aliases(self) -> None:
        term = NewTerm(
            slug="tick", name="Tick", definition="a tick", aliases=("instant", "Instant")
        )
        assert term.aliases == ("instant",)


class TestInterpret:
    def test_binds_a_disciplined_utterance(self) -> None:
        reading = interpret("commit the parent node onto the tape", terms(), now=NOW)
        assert reading.is_complete
        assert reading.bound_slugs == ("commit", "parent", "node", "tape")
        assert reading.unbound == ()

    def test_joins_a_bigram_to_a_hyphenated_slug(self) -> None:
        reading = interpret("linear time protocol", terms(), now=NOW)
        assert reading.is_complete
        assert reading.bound_slugs == ("linear-time", "protocol")

    def test_resolves_an_alias_to_the_canonical_slug(self) -> None:
        reading = interpret("ltp", terms(), now=NOW)
        assert reading.is_complete
        assert reading.bound_slugs == ("linear-time",)

    def test_names_unbound_words_instead_of_guessing(self) -> None:
        """The interpreter's job is to refuse invention, not to complete it."""
        reading = interpret("hack the production database", terms(), now=NOW)
        assert not reading.is_complete
        assert set(reading.unbound) == {"hack", "production", "database"}
        assert reading.bindings == ()

    def test_stopwords_do_not_block_a_match(self) -> None:
        a = interpret("commit parent tape", terms(), now=NOW)
        b = interpret("commit the parent onto the tape", terms(), now=NOW)
        assert a.bound_slugs == b.bound_slugs == ("commit", "parent", "tape")

    def test_empty_content_is_incomplete(self) -> None:
        reading = interpret("the the the", terms(), now=NOW)
        assert not reading.is_complete
        assert reading.tokens == ()

    def test_deprecated_terms_bind_but_are_not_complete(self) -> None:
        glossary = list(terms())
        live = glossary[0]
        glossary[0] = live.deprecate()
        reading = interpret(live.slug, glossary, now=NOW)
        assert reading.bound_slugs == (live.slug,)
        assert reading.deprecated_slugs == (live.slug,)
        assert not reading.is_complete

    def test_core_glossary_has_no_colliding_keys(self) -> None:
        """Two terms claiming one word would force the interpreter to guess."""
        seen: dict[str, str] = {}
        for term in terms():
            for key in term.lookup_keys():
                owner = seen.get(key)
                assert owner is None, f"{key} claimed by {owner} and {term.slug}"
                seen[key] = term.slug

    def test_snapshots_the_definition(self) -> None:
        reading = interpret("tick", terms(), now=NOW)
        assert "strictly increasing" in reading.bindings[0].definition

    def test_rejects_a_blank_utterance(self) -> None:
        with pytest.raises(ValidationError, match="utterance"):
            interpret("   ", terms(), now=NOW)
