"""Tests for label filtering functionality."""

from __future__ import annotations

import pytest

from projects.caliper.engine.label_filters import (
    matches_filters,
    parse_filter_kv,
    parse_filter_pairs,
)


class TestParseFilterKv:
    def test_parse_valid_pairs(self):
        result = parse_filter_kv(("key1=value1", "key2=value2"))
        assert result == {"key1": "value1", "key2": "value2"}

    def test_parse_with_spaces(self):
        result = parse_filter_kv(("key1 = value1 ", " key2=value2"))
        assert result == {"key1": "value1", "key2": "value2"}

    def test_parse_equals_in_value(self):
        result = parse_filter_kv(("key1=value=with=equals",))
        assert result == {"key1": "value=with=equals"}

    def test_parse_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid filter"):
            parse_filter_kv(("invalid_pair",))


class TestParseFilterPairs:
    def test_single_values(self):
        result = parse_filter_pairs(("platform=OCP", "version=1.0"), "include")
        assert result == {"platform": ["OCP"], "version": ["1.0"]}

    def test_duplicate_keys_grouped(self):
        result = parse_filter_pairs(
            ("version=3.5.0-ga.rc2", "version=3.4-ea.2", "platform=OCP"), "include"
        )
        assert result == {"version": ["3.5.0-ga.rc2", "3.4-ea.2"], "platform": ["OCP"]}

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid include filter"):
            parse_filter_pairs(("invalid",), "include")


class TestMatchesFilters:
    def test_include_all_match(self):
        labels = {"platform": "A100", "version": "1.0"}
        include = {"platform": ["A100"], "version": ["1.0"]}
        assert matches_filters(labels, include=include, exclude={})

    def test_include_partial_match(self):
        labels = {"platform": "A100", "version": "1.0"}
        include = {"platform": ["A100"], "version": ["2.0"]}
        assert not matches_filters(labels, include=include, exclude={})

    def test_include_multi_value_or_logic(self):
        """Two values for the same key: either satisfies the filter."""
        labels = {"platform": "A100", "version": "3.4-ea.2"}
        include = {"version": ["3.5.0-ga.rc2", "3.4-ea.2"]}
        assert matches_filters(labels, include=include, exclude={})

    def test_include_multi_value_none_match(self):
        labels = {"platform": "A100", "version": "2.0"}
        include = {"version": ["3.5.0-ga.rc2", "3.4-ea.2"]}
        assert not matches_filters(labels, include=include, exclude={})

    def test_exclude_match(self):
        labels = {"platform": "A100", "version": "1.0"}
        exclude = {"platform": ["A100"]}
        assert not matches_filters(labels, include={}, exclude=exclude)

    def test_exclude_wins_over_include(self):
        labels = {"platform": "A100"}
        include = {"platform": ["A100"]}
        exclude = {"platform": ["A100"]}
        assert not matches_filters(labels, include=include, exclude=exclude)

    def test_empty_include(self):
        labels = {"platform": "A100"}
        assert matches_filters(labels, include={}, exclude={})

    def test_numeric_label_string_filter(self):
        """YAML parses '3.4' as float; string filter '3.4' should still match."""
        labels = {"version": 3.4}
        include = {"version": ["3.4"]}
        assert matches_filters(labels, include=include, exclude={})

    def test_exclude_numeric_label(self):
        """Exclude filter '3.4' should exclude a record with float label 3.4."""
        labels = {"version": 3.4}
        exclude = {"version": ["3.4"]}
        assert not matches_filters(labels, include={}, exclude=exclude)

    def test_not_set_include_matches_missing_field(self):
        labels = {"platform": "A100"}
        include = {"gpu": ["not-set"]}
        assert matches_filters(labels, include=include, exclude={})

    def test_not_set_include_does_not_match_present_field(self):
        labels = {"platform": "A100", "gpu": "H100"}
        include = {"gpu": ["not-set"]}
        assert not matches_filters(labels, include=include, exclude={})

    def test_not_set_exclude_excludes_missing_field(self):
        labels = {"platform": "A100"}
        exclude = {"gpu": ["not-set"]}
        assert not matches_filters(labels, include={}, exclude=exclude)

    def test_not_set_exclude_does_not_exclude_present_field(self):
        labels = {"platform": "A100", "gpu": "H100"}
        exclude = {"gpu": ["not-set"]}
        assert matches_filters(labels, include={}, exclude=exclude)

    def test_regular_value_matching_still_works(self):
        labels = {"platform": "A100", "version": "1.0"}
        include = {"platform": ["A100"], "gpu": ["not-set"]}
        assert matches_filters(labels, include=include, exclude={})

    def test_not_set_with_multiple_filters(self):
        labels = {"platform": "A100"}
        include = {"platform": ["A100"], "gpu": ["not-set"]}
        exclude = {"version": ["not-set"]}  # version is missing → excluded
        assert not matches_filters(labels, include=include, exclude=exclude)
