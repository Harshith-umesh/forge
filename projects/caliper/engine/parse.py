"""Parse orchestration: traverse → plugin → unified model → cache."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from projects.caliper.engine.cache import (
    cache_path_for_test_base,
    fingerprint_test_base,
    read_test_base_cache,
    test_base_cache_is_valid,
    write_test_base_cache,
)
from projects.caliper.engine.model import UnifiedRunModel
from projects.caliper.engine.parameter_matrix import (
    analyze_parameter_matrix,
    format_parameter_matrix_summary,
)
from projects.caliper.engine.traverse import discover_test_bases

logger = logging.getLogger(__name__)


def run_parse(
    *,
    base_dir: Path,
    plugin_module: str,
    plugin: object,
    use_cache: bool,
    force_report_partial: bool = True,
    show_parameter_matrix: bool = True,
    include_label_filter: dict[str, list[str]] | None = None,
    exclude_label_filter: dict[str, list[str]] | None = None,
    verbose_parsing: bool = False,
) -> UnifiedRunModel:
    """
    Run full parse or load valid cache.

    plugin must implement parse(nodes).
    """
    base_dir = base_dir.resolve()

    # Discover test bases
    nodes, excluded_dirs = discover_test_bases(
        base_dir,
        include_label_filter=include_label_filter,
        exclude_label_filter=exclude_label_filter,
    )

    # Always use per-test-base caching
    all_records = []
    cache_refs = []
    all_warnings = []

    # Track timing statistics
    parse_start_time = time.time()
    cached_count = 0
    parsed_count = 0

    parse_fn = plugin.parse

    for node in nodes:
        start_time = time.time()
        test_base_dir = node.directory
        cache_file = cache_path_for_test_base(test_base_dir, plugin_module)
        fp = fingerprint_test_base(test_base_dir, plugin_module)

        # Try to load from cache
        cached_records = None
        if use_cache:
            raw = read_test_base_cache(test_base_dir, plugin_module)
            if raw is not None and test_base_cache_is_valid(
                raw,
                expected_fingerprint=fp,
                plugin_module=plugin_module,
                test_base_dir=test_base_dir,
            ):
                cached_records = raw["records"]

        if cached_records is not None:
            # Use cached records
            all_records.extend(cached_records)
            cache_refs.append(str(cache_file))
            elapsed_time = time.time() - start_time
            cached_count += 1
            if verbose_parsing:
                relative_path = test_base_dir.relative_to(base_dir)
                logger.info(
                    f"⏱️  Directory parsed from cache in {elapsed_time:.3f}s: {relative_path}"
                )
        else:
            # Configure plugin parser logging level based on verbose_parsing
            # Remove .plugin suffix if present to match actual module structure
            base_plugin_module = plugin_module.replace(".plugin", "")
            plugin_logger_name = f"{base_plugin_module}.parsing.parsers"
            plugin_logger = logging.getLogger(plugin_logger_name)
            original_level = plugin_logger.level

            if not verbose_parsing:
                plugin_logger.setLevel(logging.WARNING)

            try:
                # Parse this test base
                result = parse_fn([node])  # Parse just this node
                records = result.records
                warnings = getattr(result, "warnings", [])
            finally:
                # Restore original log level
                plugin_logger.setLevel(original_level)

            all_records.extend(records)
            all_warnings.extend(warnings)

            # Write cache for this test base
            cache_file = write_test_base_cache(
                test_base_dir,
                plugin_module=plugin_module,
                test_base_records=records,
                fingerprint=fp,
            )
            cache_refs.append(str(cache_file))
            elapsed_time = time.time() - start_time
            parsed_count += 1
            if verbose_parsing:
                relative_path = test_base_dir.relative_to(base_dir)
                logger.info(f"⏱️  Directory parsed fresh in {elapsed_time:.3f}s: {relative_path}")

    # Log timing summary
    total_parse_time = time.time() - parse_start_time
    if verbose_parsing:
        logger.info(
            f"⏱️  Parse timing summary: {total_parse_time:.3f}s total ({cached_count} cached, {parsed_count} parsed)"
        )

    # Log excluded directories if verbose parsing is enabled or if there are exclusions
    if excluded_dirs and (verbose_parsing or len(excluded_dirs) > 0):
        logger.info(
            f"📁 Found {len(nodes)} test directories, excluded {len(excluded_dirs)} directories:"
        )

        # Group by exclusion reason
        by_reason = {}
        for excluded in excluded_dirs:
            reason = excluded["reason"]
            by_reason.setdefault(reason, []).append(excluded)

        for reason, dirs in by_reason.items():
            logger.info(f"   • {reason}: {len(dirs)} directories")
            if verbose_parsing:
                for d in dirs[:5]:  # Show first 5 examples
                    logger.info(f"     - {d['path']}: {d['detail']}")
                if len(dirs) > 5:
                    logger.info(f"     ... and {len(dirs) - 5} more")

    # Create unified model with all records
    cache_ref_summary = f"per-test-base: {len(cache_refs)} cache files"
    model = UnifiedRunModel(
        plugin_module=plugin_module,
        base_directory=str(base_dir),
        test_nodes=nodes,
        unified_result_records=all_records,
        parse_cache_ref=cache_ref_summary,
        excluded_test_directories=excluded_dirs,
    )

    if all_warnings and force_report_partial:
        for w in all_warnings:
            logger.warning(f"[parse warning] {w}")

    # Show parameter matrix if requested
    if show_parameter_matrix:
        logger.info("")
        matrix_analysis = analyze_parameter_matrix(all_records)
        matrix_summary = format_parameter_matrix_summary(matrix_analysis)
        logger.info(matrix_summary)
        logger.info("")

    return model
