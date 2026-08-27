"""Include/exclude filters on distinguishing labels."""

from __future__ import annotations

from typing import Any

LabelMap = dict[str, Any]

# Filters are grouped by key; values within a key use OR logic,
# keys across the filter use AND logic.
FilterMap = dict[str, list[str]]


def parse_filter_kv(pairs: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"Invalid filter (expected KEY=VALUE): {p}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_filter_pairs(pairs: tuple[str, ...], filter_type: str) -> FilterMap:
    """Parse filter pairs into a grouped dict of key → [values].

    Duplicate keys are collected into a list; matching any value in the list
    satisfies the filter for that key (OR within key, AND across keys).

    Args:
        pairs: Tuple of filter pairs in "key=value" format
        filter_type: Type of filter for error messages ("include" or "exclude")

    Returns:
        Dict mapping each key to a list of acceptable values
    """
    filters: FilterMap = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid {filter_type} filter format '{pair}'. Use key=value format.")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid {filter_type} filter format '{pair}'. Use key=value format.")
        filters.setdefault(key, []).append(value)
    return filters


def matches_filters(
    labels: LabelMap,
    *,
    include: FilterMap,
    exclude: FilterMap,
) -> bool:
    """Exclude wins on conflict; include requires all keys to match when non-empty.

    For each key, any value in the list satisfies the filter (OR within key).
    All keys must be satisfied (AND across keys).

    Special filter value 'not-set' matches when the field is missing from labels.

    CLI filter values are always strings, but YAML may parse bare numbers (e.g. 3.4)
    as int/float. Comparison is done via str() so '3.4' matches float 3.4.
    """

    def _matches_any(labels: LabelMap, key: str, filter_values: list[str]) -> bool:
        """Check if a label matches any of the filter values."""
        raw = labels.get(key)
        for filter_value in filter_values:
            if filter_value == "not-set":
                if key not in labels:
                    return True
            elif str(raw) == filter_value:
                return True
        return False

    for k, vs in exclude.items():
        if _matches_any(labels, k, vs):
            return False
    if not include:
        return True
    return all(_matches_any(labels, k, vs) for k, vs in include.items())


def filter_records(
    records: list[Any],
    *,
    include: FilterMap,
    exclude: FilterMap,
) -> list[Any]:
    out: list[Any] = []
    for r in records:
        if matches_filters(
            getattr(r, "distinguishing_labels", {}),
            include=include,
            exclude=exclude,
        ):
            out.append(r)
    return out
