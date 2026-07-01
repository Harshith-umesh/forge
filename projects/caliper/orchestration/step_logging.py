"""
Step-specific logging utilities for Caliper postprocessing orchestration.

Provides context managers to capture logs from individual postprocessing steps
into dedicated files (e.g., 000__caliper_parse.log, 001__caliper_visualize.log).
"""

import glob
import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Thread-local storage for step-specific handlers
_step_local_handlers = threading.local()


class StepLocalHandler(logging.Handler):
    """A logging handler that routes messages to step-specific files"""

    def __init__(self):
        super().__init__()

    def emit(self, record):
        # Only emit if we have a thread-local file handler for this step
        if hasattr(_step_local_handlers, "file_handler"):
            try:
                _step_local_handlers.file_handler.emit(record)
            except Exception:
                # Ignore errors in logging to avoid breaking execution
                pass


# Global step handler instance (shared across all steps)
_step_handler = None


def _get_next_step_index(output_dir: Path) -> int:
    """
    Determine the next available step index by examining existing log files.

    Uses glob pattern to find existing log files with format: [0-9][0-9][0-9]__*.log
    and returns the next sequential index.

    Args:
        output_dir: Directory to search for existing step log files

    Returns:
        Next available step index (0-based)
    """
    if not output_dir.exists():
        return 0

    # Find all existing step log files using glob pattern
    pattern = str(output_dir / "[0-9][0-9][0-9]__*.log")
    existing_files = glob.glob(pattern)

    if not existing_files:
        return 0

    # Extract step indices from filenames
    indices = []
    for file_path in existing_files:
        filename = Path(file_path).name
        # Extract the first 3 characters which should be the step index
        try:
            index_str = filename[:3]
            if index_str.isdigit():
                indices.append(int(index_str))
        except (ValueError, IndexError):
            # Skip files that don't match the expected format
            continue

    if not indices:
        return 0

    # Return the next sequential index
    return max(indices) + 1


def _ensure_step_handler():
    """Ensure the global step handler is attached to the root logger"""
    global _step_handler
    if _step_handler is None:
        # Create and attach the step handler to the root logger
        _step_handler = StepLocalHandler()
        _step_handler.setLevel(logging.DEBUG)

        # Get the root logger to capture all logging from any module
        root_logger = logging.getLogger()

        # Check if our handler is already added
        has_step_handler = any(isinstance(h, StepLocalHandler) for h in root_logger.handlers)

        if not has_step_handler:
            root_logger.addHandler(_step_handler)


@contextmanager
def step_logging_indexed(
    step_name: str, step_index: int, output_dir: Path
) -> Generator[Path, None, None]:
    """
    Context manager for step-specific logging with explicit step index.

    Captures all logging output during the context and writes it to a dedicated
    file named with the pattern: {step_index:03d}__{step_name}.log

    Args:
        step_name: Name of the step (e.g., "caliper_parse", "caliper_visualize")
        step_index: Zero-based index of the step for ordering (e.g., 0, 1, 2...)
        output_dir: Directory where the log file should be created

    Returns:
        Path to the log file being written to

    Example:
        with step_logging_indexed("caliper_parse", 0, output_dir) as log_file:
            logger.info("This will go to 000__caliper_parse.log")
            run_parse_operations()
    """
    # Ensure the global step handler is attached
    _ensure_step_handler()

    # Create the log file path
    log_filename = f"{step_index:03d}__{step_name}.log"
    log_file = output_dir / log_filename

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create file handler for this step
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    # Use same format as console output (no timestamp prefix, just the message)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    # Store the file handler in thread-local storage
    _step_local_handlers.file_handler = file_handler

    try:
        yield log_file
    finally:
        # Clean up: close and remove the thread-local handler
        if hasattr(_step_local_handlers, "file_handler"):
            _step_local_handlers.file_handler.close()
            del _step_local_handlers.file_handler


@contextmanager
def step_logging(step_name: str, output_dir: Path) -> Generator[Path, None, None]:
    """
    Context manager for step-specific logging with automatic step index determination.

    Automatically determines the next step index by examining existing log files
    in the output directory using glob pattern [0-9][0-9][0-9]__*.log

    Args:
        step_name: Name of the step (e.g., "caliper_parse", "caliper_visualize")
        output_dir: Directory where the log file should be created

    Returns:
        Path to the log file being written to

    Example:
        with step_logging("caliper_parse", output_dir) as log_file:
            logger.info("This will go to 000__caliper_parse.log or next available index")
            run_parse_operations()
    """
    # Get the next available step index
    step_index = _get_next_step_index(output_dir)

    # Use the indexed step_logging function with the determined index
    with step_logging_indexed(step_name, step_index, output_dir) as log_file:
        yield log_file


def cleanup_step_logging():
    """
    Clean up step logging resources.

    Call this when postprocessing is complete to remove the global handler
    and prevent memory leaks.
    """
    global _step_handler
    if _step_handler is not None:
        root_logger = logging.getLogger()
        if _step_handler in root_logger.handlers:
            root_logger.removeHandler(_step_handler)
        _step_handler = None

    # Clean up thread-local handler if it exists
    if hasattr(_step_local_handlers, "file_handler"):
        _step_local_handlers.file_handler.close()
        del _step_local_handlers.file_handler


def _log_command_banner(
    step_name: str,
    command_line: str,
    description: str = "",
    step_args: dict[str, Any] | None = None,
) -> None:
    """
    Log a command reproduction banner at the start of a step.

    Args:
        step_name: Name of the step (e.g., "caliper_parse")
        command_line: The CLI command to reproduce this step
        description: Brief description of what this step does
        step_args: Dictionary of step arguments for reference
    """
    logger = logging.getLogger(__name__)

    LINE_WIDTH = 80

    logger.info("")
    logger.info("=" * LINE_WIDTH)
    logger.info(f"CALIPER STEP: {step_name.upper()}")
    if description:
        logger.info(f"DESCRIPTION: {description}")
    logger.info("=" * LINE_WIDTH)
    logger.info(f"COMMAND TO REPRODUCE:\n{command_line}")

    if step_args:
        logger.info("")
        logger.info("STEP PARAMETERS:")
        for key, value in step_args.items():
            if value is not None:
                # Truncate very long values
                value_str = str(value)
                if len(value_str) > 100:
                    value_str = value_str[:97] + "..."
                logger.info(f"  {key}: {value_str}")

    logger.info("=" * LINE_WIDTH)
    logger.info("")


def log_parse_command(
    base_dir: Path,
    plugin_module: str,
    use_cache: bool,
    manifest_path: Path | None = None,
) -> None:
    """Log the CLI command to reproduce the parse step."""
    cache_flag = "" if use_cache else " --no-cache"
    command = f'caliper parse --base-dir "{base_dir}" --plugin {plugin_module}{cache_flag}'

    step_args = {
        "base_dir": str(base_dir),
        "plugin_module": plugin_module,
        "use_cache": use_cache,
    }

    # Only include manifest_path if it's provided and safe to log
    if manifest_path:
        step_args["manifest_path"] = str(manifest_path)

    _log_command_banner(
        "caliper_parse",
        command,
        "Parse test results from artifact tree into unified model",
        step_args,
    )


def log_visualize_command(
    base_dir: Path,
    plugin_module: str,
    output_dir: Path,
    reports_csv: str | None = None,
    report_group: str | None = None,
    visualize_config_path: Path | None = None,
    include_pairs: tuple[str, ...] = (),
    exclude_pairs: tuple[str, ...] = (),
    use_cache: bool = True,
) -> None:
    """Log the CLI command to reproduce the visualize step."""
    command_parts = [
        "caliper visualize",
        f'--base-dir "{base_dir}"',
        f'--output-dir "{output_dir}"',
        f"--plugin {plugin_module}",
    ]

    if not use_cache:
        command_parts.append("--no-cache")
    if reports_csv:
        command_parts.append(f'--reports "{reports_csv}"')
    if report_group:
        command_parts.append(f'--report-group "{report_group}"')
    if visualize_config_path:
        command_parts.append(f'--visualize-config "{visualize_config_path}"')

    for include_pair in include_pairs:
        command_parts.append(f'--include "{include_pair}"')
    for exclude_pair in exclude_pairs:
        command_parts.append(f'--exclude "{exclude_pair}"')

    command = " \\\n    ".join(command_parts)

    step_args = {
        "base_dir": str(base_dir),
        "plugin_module": plugin_module,
        "output_dir": str(output_dir),
        "use_cache": use_cache,
    }

    # Only include optional parameters if they're provided
    if reports_csv:
        step_args["reports_csv"] = reports_csv
    if report_group:
        step_args["report_group"] = report_group
    if visualize_config_path:
        step_args["visualize_config_path"] = str(visualize_config_path)
    if include_pairs:
        step_args["include_pairs"] = list(include_pairs)
    if exclude_pairs:
        step_args["exclude_pairs"] = list(exclude_pairs)

    _log_command_banner(
        "caliper_visualize",
        command,
        "Generate visualization reports and plots from parsed data",
        step_args,
    )


def log_kpi_generate_command(
    base_dir: Path,
    plugin_module: str,
    output_file: Path,
) -> None:
    """Log the CLI command to reproduce the KPI generation step."""
    command = f'caliper kpi generate --base-dir "{base_dir}" --plugin {plugin_module} --output "{output_file}"'

    step_args = {
        "base_dir": str(base_dir),
        "plugin_module": plugin_module,
        "output_file": str(output_file),
    }

    _log_command_banner(
        "caliper_kpi_generate", command, "Generate KPI metrics from parsed test results", step_args
    )


def log_kpi_export_command(
    input_path: Path,
    target_system: str = "opensearch",
) -> None:
    """Log the CLI command to reproduce the KPI export step."""
    command = f'caliper kpi export --input "{input_path}" --target {target_system}'

    step_args = {
        "input_path": str(input_path),
        "target_system": target_system,
    }

    _log_command_banner(
        "caliper_kpi_export", command, "Export KPI metrics to external monitoring system", step_args
    )


def log_ai_eval_command(
    base_dir: Path,
    plugin_module: str,
    output_file: Path,
) -> None:
    """Log the CLI command to reproduce the AI evaluation export step."""
    command = f'caliper ai-eval export --base-dir "{base_dir}" --plugin {plugin_module} --output "{output_file}"'

    step_args = {
        "base_dir": str(base_dir),
        "plugin_module": plugin_module,
        "output_file": str(output_file),
    }

    _log_command_banner(
        "caliper_ai_eval_export",
        command,
        "Export AI evaluation payload for external analysis",
        step_args,
    )


def log_analyze_command(
    base_dir: Path,
    plugin_module: str,
) -> None:
    """Log the CLI command to reproduce the analyze step."""
    command = f'caliper analyze --base-dir "{base_dir}" --plugin {plugin_module}'

    step_args = {
        "base_dir": str(base_dir),
        "plugin_module": plugin_module,
    }

    _log_command_banner(
        "caliper_analyze", command, "Run regression analysis on test results", step_args
    )


def log_kpi_csv_export_command(
    input_path: Path,
    output_path: Path,
) -> None:
    """Log the CLI command to reproduce the KPI CSV export step."""
    command = f'caliper kpi csv-export --input "{input_path}" --output "{output_path}"'

    step_args = {
        "input_path": str(input_path),
        "output_path": str(output_path),
    }

    _log_command_banner(
        "caliper_kpi_csv_export", command, "Export KPI metrics to CSV format", step_args
    )
