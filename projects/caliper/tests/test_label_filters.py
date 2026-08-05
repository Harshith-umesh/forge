"""Tests for label filtering functionality."""

from __future__ import annotations

import pytest

from projects.caliper.engine.label_filters import (
    matches_filters,
    parse_filter_kv,
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


class TestMatchesFilters:
    def test_include_all_match(self):
        labels = {"platform": "A100", "version": "1.0"}
        include = {"platform": "A100", "version": "1.0"}
        exclude = {}
        assert matches_filters(labels, include=include, exclude=exclude)

    def test_include_partial_match(self):
        labels = {"platform": "A100", "version": "1.0"}
        include = {"platform": "A100", "version": "2.0"}
        exclude = {}
        assert not matches_filters(labels, include=include, exclude=exclude)

    def test_exclude_match(self):
        labels = {"platform": "A100", "version": "1.0"}
        include = {}
        exclude = {"platform": "A100"}
        assert not matches_filters(labels, include=include, exclude=exclude)

    def test_exclude_wins_over_include(self):
        labels = {"platform": "A100"}
        include = {"platform": "A100"}
        exclude = {"platform": "A100"}
        assert not matches_filters(labels, include=include, exclude=exclude)

    def test_empty_include(self):
        labels = {"platform": "A100"}
        include = {}
        exclude = {}
        assert matches_filters(labels, include=include, exclude=exclude)

    def test_not_set_include_matches_missing_field(self):
        """Test that 'not-set' in include filter matches when field is missing."""
        labels = {"platform": "A100"}
        include = {"gpu": "not-set"}
        exclude = {}
        assert matches_filters(labels, include=include, exclude=exclude)

    def test_not_set_include_does_not_match_present_field(self):
        """Test that 'not-set' in include filter does not match when field is present."""
        labels = {"platform": "A100", "gpu": "H100"}
        include = {"gpu": "not-set"}
        exclude = {}
        assert not matches_filters(labels, include=include, exclude=exclude)

    def test_not_set_exclude_excludes_missing_field(self):
        """Test that 'not-set' in exclude filter excludes when field is missing."""
        labels = {"platform": "A100"}
        include = {}
        exclude = {"gpu": "not-set"}
        assert not matches_filters(labels, include=include, exclude=exclude)

    def test_not_set_exclude_does_not_exclude_present_field(self):
        """Test that 'not-set' in exclude filter does not exclude when field is present."""
        labels = {"platform": "A100", "gpu": "H100"}
        include = {}
        exclude = {"gpu": "not-set"}
        assert matches_filters(labels, include=include, exclude=exclude)

    def test_regular_value_matching_still_works(self):
        """Test that regular value matching still works alongside not-set functionality."""
        labels = {"platform": "A100", "version": "1.0"}
        include = {"platform": "A100", "gpu": "not-set"}
        exclude = {}
        assert matches_filters(labels, include=include, exclude=exclude)

    def test_not_set_with_multiple_filters(self):
        """Test not-set works in combination with other filters."""
        labels = {"platform": "A100"}
        include = {"platform": "A100", "gpu": "not-set"}
        exclude = {"version": "not-set"}  # Should exclude because version is also missing
        assert not matches_filters(labels, include=include, exclude=exclude)
