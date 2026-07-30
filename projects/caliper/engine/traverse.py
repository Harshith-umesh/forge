"""Discover test base directories via __test_labels__.yaml or MatrixBenchmarking settings.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from projects.caliper.engine.model import TestBaseNode

MARKER = "__test_labels__.yaml"
MATRIXBENCHMARKING_MARKER = "settings.yaml"


def discover_test_bases(
    base_dir: Path, *, label_filter: dict[str, Any] | None = None
) -> list[TestBaseNode]:
    """Walk base_dir; each directory containing MARKER or MATRIXBENCHMARKING_MARKER becomes a TestBaseNode.

    Args:
        base_dir: Base directory to search
        label_filter: Optional dict of label key-value pairs that must match for inclusion
    """
    base_dir = base_dir.resolve()
    if not base_dir.is_dir():
        raise FileNotFoundError(f"Base directory does not exist: {base_dir}")

    nodes: list[TestBaseNode] = []
    for dirpath, _dirnames, filenames in os.walk(base_dir, topdown=True):
        marker_found = None
        if MARKER in filenames:
            marker_found = MARKER
        elif MATRIXBENCHMARKING_MARKER in filenames:
            marker_found = MATRIXBENCHMARKING_MARKER

        if marker_found is None:
            continue

        path = Path(dirpath)
        marker_path = path / marker_found
        # Apply label filtering if specified
        if label_filter is not None and not _matches_label_filter(labels, label_filter):
            continue

        nodes.append(
            TestBaseNode(
                directory=path,
                labels=labels,
                artifact_paths=_list_files_under(path, exclude_markers=True),
                test_path=path.relative_to(base_dir),
            )
        )
    return sorted(nodes, key=lambda n: str(n.directory))


def _load_hierarchical_labels(test_dir: Path, base_dir: Path) -> dict[str, Any]:
    """Load and merge __test_labels__.yaml files hierarchically from base_dir down to test_dir.

    Merges in order:
    1. base_dir/__test_labels__.*.yaml (all variants)
    2. parent_dir/__test_labels__.*.yaml (all variants)
    3. test_dir/__test_labels__.*.yaml (all variants)
    4. test_dir/__test_labels__.yaml (final, cannot be overridden)

    Later files override earlier ones, with the main __test_labels__.yaml having final priority.
    """
    import glob

    merged_labels: dict[str, Any] = {}

    # Get all directories from base_dir down to test_dir (inclusive)
    test_dir_abs = test_dir.resolve()
    base_dir_abs = base_dir.resolve()

    # Build path from base_dir to test_dir
    try:
        rel_path = test_dir_abs.relative_to(base_dir_abs)
        path_parts = [base_dir_abs] + [
            base_dir_abs / Path(*rel_path.parts[: i + 1]) for i in range(len(rel_path.parts))
        ]
    except ValueError:
        # test_dir is not under base_dir, just use test_dir
        path_parts = [test_dir_abs]

    # For each directory in the hierarchy, merge __test_labels__.*.yaml files (excluding plain __test_labels__.yaml)
    for dir_path in path_parts:
        if not dir_path.is_dir():
            continue

        # Find all __test_labels__.*.yaml files (but not __test_labels__.yaml itself)
        pattern = str(dir_path / "__test_labels__.*.yaml")
        variant_files = sorted(glob.glob(pattern))

        for variant_file in variant_files:
            variant_path = Path(variant_file)
            if variant_path.is_file():
                try:
                    variant_labels = _load_labels(variant_path, is_matrixbenchmarking=False)
                    # Merge the labels (later values override earlier ones)
                    _deep_merge_dict(merged_labels, variant_labels)
                except (OSError, yaml.YAMLError, ValueError):
                    # Skip files that can't be loaded
                    pass

    # Finally, load the main __test_labels__.yaml from the test directory (final priority)
    main_labels_path = test_dir / MARKER
    if main_labels_path.is_file():
        try:
            main_labels = _load_labels(main_labels_path, is_matrixbenchmarking=False)
            _deep_merge_dict(merged_labels, main_labels)
        except (OSError, yaml.YAMLError, ValueError):
            # If main file can't be loaded, return what we have
            pass

    return merged_labels


def _load_hierarchical_labels_matrixbenchmarking(test_dir: Path, base_dir: Path) -> dict[str, Any]:
    """Load and merge settings.yaml files hierarchically from base_dir down to test_dir for MatrixBenchmarking.

    Merges in order:
    1. base_dir/settings.*.yaml (all variants)
    2. parent_dir/settings.*.yaml (all variants)
    3. test_dir/settings.*.yaml (all variants)
    4. test_dir/settings.yaml (final, cannot be overridden)

    Later files override earlier ones, with the main settings.yaml having final priority.
    """
    import glob

    merged_labels: dict[str, Any] = {}

    # Get all directories from base_dir down to test_dir (inclusive)
    test_dir_abs = test_dir.resolve()
    base_dir_abs = base_dir.resolve()

    # Build path from base_dir to test_dir
    try:
        rel_path = test_dir_abs.relative_to(base_dir_abs)
        path_parts = [base_dir_abs] + [
            base_dir_abs / Path(*rel_path.parts[: i + 1]) for i in range(len(rel_path.parts))
        ]
    except ValueError:
        # test_dir is not under base_dir, just use test_dir
        path_parts = [test_dir_abs]

    # For each directory in the hierarchy, merge settings.*.yaml files (excluding plain settings.yaml)
    for dir_path in path_parts:
        if not dir_path.is_dir():
            continue

        # Find all settings.*.yaml files (but not settings.yaml itself)
        pattern = str(dir_path / "settings.*.yaml")
        variant_files = sorted(glob.glob(pattern))

        for variant_file in variant_files:
            variant_path = Path(variant_file)
            if variant_path.is_file():
                try:
                    variant_labels = _load_labels(variant_path, is_matrixbenchmarking=True)
                    # Merge the labels (later values override earlier ones)
                    _deep_merge_dict(merged_labels, variant_labels)
                except (OSError, yaml.YAMLError, ValueError):
                    # Skip files that can't be loaded
                    pass

    # Finally, load the main settings.yaml from the test directory (final priority)
    main_labels_path = test_dir / MATRIXBENCHMARKING_MARKER
    if main_labels_path.is_file():
        try:
            main_labels = _load_labels(main_labels_path, is_matrixbenchmarking=True)
            _deep_merge_dict(merged_labels, main_labels)
        except (OSError, yaml.YAMLError, ValueError):
            # If main file can't be loaded, return what we have
            pass

    return merged_labels


def _matches_label_filter(labels: dict[str, Any], label_filter: dict[str, Any]) -> bool:
    """Check if labels match the specified filter criteria.

    Args:
        labels: The hierarchically merged labels from the test directory
        label_filter: Dict of key-value pairs that must match

    Returns:
        True if all filter criteria match, False otherwise

    Examples:
        _matches_label_filter({"labels": {"platform": "CKS", "version": "3.5"}},
                            {"platform": "CKS"}) -> True
        _matches_label_filter({"labels": {"platform": "OCP"}},
                            {"platform": "CKS"}) -> False
    """
    # Extract the labels section if it exists (for __test_labels__.yaml structure)
    actual_labels = labels.get("labels", {}) if isinstance(labels.get("labels"), dict) else labels

    for filter_key, filter_value in label_filter.items():
        # Support nested key access with dot notation (e.g., "labels.platform")
        if "." in filter_key:
            keys = filter_key.split(".")
            current = labels
            for key in keys:
                if not isinstance(current, dict) or key not in current:
                    return False
                current = current[key]
            if current != filter_value:
                return False
        else:
            # Direct key access in labels section or root level
            if filter_key not in actual_labels and filter_key not in labels:
                return False
            value = actual_labels.get(filter_key, labels.get(filter_key))
            if value != filter_value:
                return False

    return True


def _deep_merge_dict(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Deep merge source dict into target dict, with source values taking precedence."""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge_dict(target[key], value)
        else:
            target[key] = value


def _load_labels(path: Path, is_matrixbenchmarking: bool = False) -> dict[str, Any]:
    """Load labels from either __test_labels__.yaml or MatrixBenchmarking settings.yaml."""
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        return {}
    if not isinstance(data, dict):
        marker_name = MATRIXBENCHMARKING_MARKER if is_matrixbenchmarking else MARKER
        raise ValueError(f"Invalid {marker_name}: top level must be a mapping: {path}")

    # For MatrixBenchmarking settings.yaml, add metadata to distinguish the source
    if is_matrixbenchmarking:
        # Add a special label to indicate this came from MatrixBenchmarking
        result = dict(data)
        result["__caliper_source__"] = "matrixbenchmarking"
        return result

    return data


def _list_files_under(dirpath: Path, *, exclude_markers: bool) -> list[Path]:
    """List all files under dirpath, optionally excluding both marker files."""
    out: list[Path] = []
    excluded_names = {MARKER, MATRIXBENCHMARKING_MARKER} if exclude_markers else set()
    for p in sorted(dirpath.rglob("*")):
        if p.is_file() and (not exclude_markers or p.name not in excluded_names):
            out.append(p)
    return out
