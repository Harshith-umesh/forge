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

    Captures ALL logging output during the context and writes it to a dedicated
    file named with the pattern: {step_index:03d}__{step_name}.log

    This context manager temporarily configures the entire logging system to
    ensure that ALL log messages from any logger get captured to the step log file.

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

    # Get the root logger to capture everything
    root_logger = logging.getLogger()

    # Save original state to restore later
    original_handlers = root_logger.handlers.copy()

    # Note: We now use direct root logger capture instead of the StepLocalHandler mechanism
    # This provides more reliable and comprehensive log capture

    try:
        # Add our file handler directly to the root logger
        root_logger.addHandler(file_handler)

        # Save propagation states and ensure loggers propagate to root
        saved_logger_states = {}

        # Get all existing loggers from the logger registry
        for logger_name in logging.getLogger().manager.loggerDict:
            existing_logger = logging.getLogger(logger_name)
            # Save original state (only propagate, don't touch levels)
            saved_logger_states[logger_name] = {
                "propagate": existing_logger.propagate,
            }
            # Only ensure propagation, don't change levels
            existing_logger.propagate = True

        yield log_file

    finally:
        # Remove our file handler from root logger
        if file_handler in root_logger.handlers:
            root_logger.removeHandler(file_handler)

        # Restore original handlers completely
        # (Remove any handlers that were added, restore original list)
        root_logger.handlers.clear()
        root_logger.handlers.extend(original_handlers)

        # Restore original logger propagation states
        for logger_name, state in saved_logger_states.items():
            try:
                existing_logger = logging.getLogger(logger_name)
                existing_logger.propagate = state["propagate"]
            except Exception:
                # Ignore errors when restoring logger states
                pass

        # Close the file handler
        file_handler.close()


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






