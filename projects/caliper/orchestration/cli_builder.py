"""CLI command builder for Caliper orchestration fork/exec.

Converts orchestration config objects to CLI argument lists for subprocess execution.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from projects.caliper.orchestration.postprocess_config import (
    CaliperOrchestrationPostprocessConfig,
)

logger = logging.getLogger(__name__)


def build_parse_command(
    config: CaliperOrchestrationPostprocessConfig,
    tree_root: Path,
    manifest_path: Path | None,
    status_file: Path,
    use_cache: bool = True,
) -> list[str]:
    """Build CLI command for caliper parse.

    Args:
        config: Orchestration configuration
        tree_root: Base directory for artifacts
        manifest_path: Optional manifest file path
        status_file: Where to write status YAML
        use_cache: Whether to use parsing cache

    Returns:
        List of command arguments for subprocess
    """
    cmd = [
        sys.executable,
        "-m",
        "projects.caliper.cli.main",
        "parse",
    ]

    # Workspace options
    cmd.extend(["--artifacts-dir", str(tree_root)])

    if manifest_path:
        cmd.extend(["--postprocess-config", str(manifest_path)])

    if config.plugin_module:
        cmd.extend(["--plugin", config.plugin_module])

    # Parse-specific options
    if not use_cache:
        cmd.append("--no-cache")

    # Enable detailed parameter matrix output
    cmd.append("--show-matrix")

    # Status file for orchestration
    cmd.extend(["--status-file", str(status_file)])

    return cmd


def build_visualize_command(
    config: CaliperOrchestrationPostprocessConfig,
    tree_root: Path,
    manifest_path: Path | None,
    status_file: Path,
    output_dir: Path,
    use_cache: bool = True,
) -> list[str]:
    """Build CLI command for caliper visualize.

    Args:
        config: Orchestration configuration
        tree_root: Base directory for artifacts
        manifest_path: Optional manifest file path
        status_file: Where to write status YAML
        output_dir: Output directory for visualization files
        use_cache: Whether to use parsing cache

    Returns:
        List of command arguments for subprocess
    """
    cmd = [
        sys.executable,
        "-m",
        "projects.caliper.cli.main",
        "visualize",
    ]

    # Workspace options
    cmd.extend(["--artifacts-dir", str(tree_root)])

    if manifest_path:
        cmd.extend(["--postprocess-config", str(manifest_path)])

    if config.plugin_module:
        cmd.extend(["--plugin", config.plugin_module])

    # Visualize-specific options
    cmd.extend(["--output-dir", str(output_dir)])

    if config.visualize.reports:
        cmd.extend(["--reports", ",".join(config.visualize.reports)])

    if config.visualize.report_group:
        cmd.extend(["--report-group", config.visualize.report_group])

    if config.visualize.visualize_config:
        # Resolve the visualize config path properly (same logic as _resolve_visualize_config_path)
        viz_config_path = config.visualize.visualize_config
        if viz_config_path:
            from projects.core.library import env

            viz_path = Path(viz_config_path).expanduser()
            if not viz_path.is_absolute():
                # If relative, resolve relative to FORGE_HOME like the original does
                viz_path = env.FORGE_HOME / viz_path
            cmd.extend(["--visualize-config", str(viz_path.resolve())])

    # Include/exclude labels
    for label in config.visualize.include_labels:
        cmd.extend(["--include-label", label])

    for label in config.visualize.exclude_labels:
        cmd.extend(["--exclude-label", label])

    if not use_cache:
        cmd.append("--no-cache")

    # Status file for orchestration
    cmd.extend(["--status-file", str(status_file)])

    return cmd


def save_command_script(command: list[str], script_path: Path) -> None:
    """Save executed command to a shell script for debugging.

    Args:
        command: Command arguments list
        script_path: Where to save the script
    """
    script_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert command to a readable shell script
    script_content = "#!/bin/bash\n"
    script_content += "# Generated command for debugging\n"
    script_content += "# Run this command to reproduce the step manually\n\n"

    # Format command with argument pairs on separate lines
    formatted_lines = _format_command_for_script(command)
    script_content += formatted_lines + "\n"

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_content)

    # Make executable
    script_path.chmod(0o755)


def _format_command_for_script(command: list[str]) -> str:
    """Format command for script file with argument pairs on separate lines.

    Args:
        command: Command arguments list

    Returns:
        Formatted command string for shell script
    """
    if not command:
        return ""

    def escape_arg(arg: str) -> str:
        """Escape shell arguments that need quoting."""
        if " " in arg or any(c in arg for c in ["$", "`", '"', "'"]):
            return f'"{arg}"'
        return arg

    lines = []
    i = 0

    # Handle base command parts until we hit options
    base_parts = []
    while i < len(command) and not command[i].startswith("--"):
        base_parts.append(escape_arg(command[i]))
        i += 1

    if base_parts:
        lines.append(" ".join(base_parts))

    # Handle option pairs (--flag value) with line continuations
    while i < len(command):
        if command[i].startswith("--"):
            if i + 1 < len(command) and not command[i + 1].startswith("--"):
                # Flag with value
                lines.append(f"  {escape_arg(command[i])} {escape_arg(command[i + 1])}")
                i += 2
            else:
                # Boolean flag
                lines.append(f"  {escape_arg(command[i])}")
                i += 1
        else:
            # Standalone argument
            lines.append(f"  {escape_arg(command[i])}")
            i += 1

    # Join with line continuations
    if len(lines) == 1:
        return lines[0]
    else:
        return lines[0] + " \\\n" + " \\\n".join(lines[1:])


def log_caliper_start_banner(command: list[str], script_path: Path, step_name: str) -> None:
    """Log start banner and save command script for Caliper execution.

    Args:
        command: Command arguments list
        script_path: Where to save the executable script
        step_name: Name of the Caliper step (e.g., "PARSE", "VISUALIZE")
    """
    # Save command script for debugging
    save_command_script(command, script_path)

    # Format command with argument pairs on new lines
    formatted_command = _format_command_for_display(command)

    # Log banner indicating Caliper is starting
    logger.info("=" * 60)
    logger.info("🚀 STARTING CALIPER %s (fork/exec)", step_name.upper())
    logger.info("📄 Command:")
    for line in formatted_command.splitlines():
        logger.info("  %s", line)
    logger.info("=" * 60)


def _format_command_for_display(command: list[str]) -> str:
    """Format command for readable display with argument pairs on new lines.

    Args:
        command: Command arguments list

    Returns:
        Formatted command string
    """
    if not command:
        return ""

    # Start with base command (python -m module subcommand)
    lines = []
    i = 0

    # Handle base command parts until we hit options
    base_parts = []
    while i < len(command) and not command[i].startswith("--"):
        base_parts.append(command[i])
        i += 1

    if base_parts:
        lines.append(" ".join(base_parts))

    # Handle option pairs (--flag value)
    while i < len(command):
        if command[i].startswith("--"):
            if i + 1 < len(command) and not command[i + 1].startswith("--"):
                # Flag with value
                lines.append(f"{command[i]} {command[i + 1]}")
                i += 2
            else:
                # Boolean flag
                lines.append(command[i])
                i += 1
        else:
            # Standalone argument
            lines.append(command[i])
            i += 1

    return "\n".join(lines)


def handle_caliper_output_and_completion(
    result: subprocess.CompletedProcess,
    log_file: Path,
    status_file: Path,
    step_name: str,
) -> dict:
    """Handle Caliper output writing, status parsing, and log completion banner.

    Args:
        result: Subprocess result from caliper execution
        log_file: Path to write the step log file
        status_file: Path to the status file to read and parse
        step_name: Name of the Caliper step (e.g., "PARSE", "VISUALIZE")

    Returns:
        Parsed status data dictionary
    """
    import yaml

    # Write output to log file
    with open(log_file, "w", encoding="utf-8") as log_f:
        log_f.write(result.stdout)

    # Also display output in main orchestration logs
    if result.stdout:
        prefix = step_name.upper()
        for line in result.stdout.splitlines():
            logger.info("%s: %s", prefix, line)

    # Log banner indicating Caliper execution completed
    logger.info("=" * 60)
    logger.info("🏁 CALIPER %s COMPLETED", step_name.upper())
    logger.info("📊 Exit code: %s", result.returncode)
    logger.info("📄 Log file: %s", log_file)

    # Read and parse status file
    try:
        with open(status_file, encoding="utf-8") as f:
            status_data = yaml.safe_load(f)
        # Handle case where YAML file is empty or invalid
        if status_data is None:
            status_data = {
                "success": False,
                "error": "Status file is empty or contains no valid YAML data",
            }
    except Exception as e:
        status_data = {"success": False, "error": f"Failed to read status file: {e}"}

    # Log status file content for visibility
    logger.info("📄 Status file content:")
    status_yaml = yaml.dump(status_data, default_flow_style=False, sort_keys=False)
    for line in status_yaml.strip().splitlines():
        logger.info("  %s", line)

    logger.info("=" * 60)

    return status_data
