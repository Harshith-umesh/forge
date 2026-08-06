"""Include/exclude filters on distinguishing labels."""

from __future__ import annotations

from typing import Any

LabelMap = dict[str, Any]


def parse_filter_kv(pairs: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"Invalid filter (expected KEY=VALUE): {p}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_filter_pairs(pairs: tuple[str, ...], filter_type: str) -> list[dict[str, str]]:
    """Parse filter pairs into individual single-key dictionaries.

    Args:
        pairs: Tuple of filter pairs in "key=value" format
        filter_type: Type of filter for error messages ("include" or "exclude")

    Returns:
        List of single-key dictionaries, preserving repeated keys and order

    This function preserves duplicate keys and their order, unlike parse_filter_kv
    which aggregates all values into a single dictionary.
    """
    filters = []
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid {filter_type} filter format '{pair}'. Use key=value format.")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid {filter_type} filter format '{pair}'. Use key=value format.")
        filters.append({key: value})
    return filters


def matches_filters(
    labels: LabelMap,
    *,
    include: dict[str, str],
    exclude: dict[str, str],
) -> bool:
    """Exclude wins on conflict; include requires all pairs to match when non-empty.

    Special filter value 'not-set' matches when the field is missing from labels.
    """

    def _matches_value(labels: LabelMap, key: str, filter_value: str) -> bool:
        """Check if a label matches a filter value, supporting 'not-set' for missing fields."""
        if filter_value == "not-set":
            return key not in labels
        return labels.get(key) == filter_value

    for k, v in exclude.items():
        if _matches_value(labels, k, v):
            return False
    if not include:
        return True
    return all(_matches_value(labels, k, v) for k, v in include.items())


def filter_records(
    records: list[Any],
    *,
    include: dict[str, str],
    exclude: dict[str, str],
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
