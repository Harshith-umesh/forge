"""
Notification formatting for Caliper postprocess status.

Provides models and formatting functions for converting postprocess status
into GitHub notification text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PostprocessStepResult:
    """Result of a single postprocess step."""

    status: str
    message: str | None = None
    paths: list[str] | None = None
    completed_at: float | None = None
    reason: str | None = None
    output_file: str | None = None
    ai_eval_dir: str | None = None


@dataclass
class PostprocessResult:
    """Overall result of postprocess operation."""

    success: bool
    final_status: str | None = None
    steps: dict[str, PostprocessStepResult] | None = None
    test_phase: dict[str, Any] | None = None


def format_postprocess_status_notification(
    result: PostprocessResult, get_file_link: callable | None = None
) -> str:
    """Format postprocess result into notification text with file links.

    Args:
        result: Structured PostprocessResult object
        get_file_link: Optional callback function that takes a file path and returns a URL.
                      Signature: get_file_link(file_path: str) -> str

    Returns:
        Formatted notification text to include in GitHub notification
    """
    if not result:
        return ""

    lines = []

    # Check overall status
    status_emoji = "✅" if result.success else "❌"
    lines.append(f"**Post-processing Status {status_emoji}**")

    # Add steps information if available, sorted by completion time
    if result.steps:
        # Sort steps by completion timestamp (completed_at), with fallback to step name for stable ordering
        sorted_steps = sorted(
            result.steps.items(),
            key=lambda item: (
                getattr(item[1], "completed_at", 0) or 0,  # Use completed_at if available, else 0
                item[0],  # fallback to step name for stable ordering
            ),
        )

        for step_name, step_result in sorted_steps:
            step_emoji = _get_step_emoji(step_result.status)
            lines.append(f"- {step_emoji} **{step_name}**: `{step_result.status}`")

            # Add step message if available
            if step_result.message:
                lines.append(f"  > {step_result.message}")

            # Add reason for skipped steps
            if step_result.status in ("skipped", "disabled") and step_result.reason:
                lines.append(f"  > {step_result.reason}")

            # Add specific file links for certain steps
            if step_result.status == "success" and get_file_link:
                if (
                    step_name == "kpi_generate"
                    and hasattr(step_result, "output_file")
                    and step_result.output_file
                ):
                    try:
                        # Extract relative path from absolute path (assume output_file contains relative path from output directory)
                        from pathlib import Path

                        output_path = Path(step_result.output_file)
                        # For KPI files, typically just the filename is what we want
                        relative_path = output_path.name
                        file_url = get_file_link(relative_path)
                        lines.append(f"  - 📄 [{relative_path}]({file_url})")
                    except Exception:
                        filename = step_result.output_file.split("/")[-1]
                        lines.append(f"  - 📄 {filename}")

                elif (
                    step_name == "kpi_csv_export"
                    and hasattr(step_result, "output_file")
                    and step_result.output_file
                ):
                    try:
                        # Extract relative path for CSV file
                        from pathlib import Path

                        output_path = Path(step_result.output_file)
                        relative_path = output_path.name
                        file_url = get_file_link(relative_path)
                        lines.append(f"  - 📊 [{relative_path}]({file_url})")
                    except Exception:
                        filename = step_result.output_file.split("/")[-1]
                        lines.append(f"  - 📊 {filename}")

                elif step_name == "ai_eval_export":
                    if hasattr(step_result, "ai_eval_dir") and step_result.ai_eval_dir:
                        try:
                            dir_url = get_file_link("")  # Get base directory URL
                            # Extract relative path from the full path
                            ai_eval_dir_relative = step_result.ai_eval_dir.split("/")[
                                -1
                            ]  # Get just "ai_eval"
                            dir_url = get_file_link(ai_eval_dir_relative)
                            lines.append(f"  - 📁 [AI Eval Directory]({dir_url})")
                        except Exception:
                            lines.append(f"  - 📁 AI Eval Directory: {step_result.ai_eval_dir}")

                    if hasattr(step_result, "output_file") and step_result.output_file:
                        try:
                            # Extract relative path for output file
                            import os

                            output_file_relative = os.path.relpath(
                                step_result.output_file,
                                step_result.ai_eval_dir
                                if hasattr(step_result, "ai_eval_dir")
                                else "",
                            )
                            if step_result.ai_eval_dir and "ai_eval" in step_result.ai_eval_dir:
                                output_file_relative = f"ai_eval/{output_file_relative}"
                            file_url = get_file_link(output_file_relative)
                            filename = step_result.output_file.split("/")[-1]
                            lines.append(f"  - 📄 [{filename}]({file_url})")
                        except Exception:
                            filename = step_result.output_file.split("/")[-1]
                            lines.append(f"  - 📄 {filename}")

            # Add general file links if available (for visualize step, etc.)
            if step_result.paths and get_file_link:
                lines.extend(_format_step_file_links(step_name, step_result.paths, get_file_link))

    return "\n".join(lines) if lines else ""


def _get_step_emoji(status: str) -> str:
    """Get emoji for step status."""
    if status == "success":
        return "✅"
    elif status in ("failed", "failure"):
        return "❌"
    elif status in ("skipped", "disabled"):
        return "⏭️"
    else:
        return "⚠️"


def _format_step_file_links(
    step_name: str, file_paths: list[str], get_file_link: callable
) -> list[str]:
    """Format file paths as clickable links using the provided callback.

    Args:
        step_name: Name of the step
        file_paths: List of relative file paths
        get_file_link: Callback function to generate URLs from file paths

    Returns:
        List of formatted link strings
    """
    if not file_paths:
        return []

    lines = []

    # Group files by type for better organization
    file_groups = _group_files_by_type(file_paths)

    # Flatten the structure - just list all files without grouping by type
    for file_type, files in file_groups.items():
        for file_path in files:
            try:
                file_url = get_file_link(file_path)
                file_name = _get_display_name(file_path)
                emoji = "📊" if file_type == "visualization" else "📄"
                lines.append(f"  - {emoji} [{file_name}]({file_url})")
            except Exception:
                # Fallback to plain text if link generation fails
                file_name = _get_display_name(file_path)
                emoji = "📊" if file_type == "visualization" else "📄"
                lines.append(f"  - {emoji} {file_name}")

    return lines


def _group_files_by_type(file_paths: list[str]) -> dict[str, list[str]]:
    """Group file paths by their type based on extension."""
    groups = {}

    for file_path in file_paths:
        file_type = _get_file_type(file_path)
        if file_type not in groups:
            groups[file_type] = []
        groups[file_type].append(file_path)

    return groups


def _get_file_type(file_path: str) -> str:
    """Determine file type from path."""
    from pathlib import Path

    ext = Path(file_path).suffix.lower()

    if ext in (".html", ".htm"):
        return "report"
    elif ext in (".png", ".jpg", ".jpeg", ".svg", ".pdf"):
        return "visualization"
    elif ext in (".json", ".yaml", ".yml"):
        return "data"
    elif ext in (".csv", ".tsv"):
        return "table"
    elif ext in (".txt", ".log"):
        return "log"
    else:
        return "file"


def _get_display_name(file_path: str) -> str:
    """Get display name for a file path."""
    from pathlib import Path

    path = Path(file_path)

    # For files in subdirectories, show parent/filename for context
    if len(path.parts) > 1:
        parent = path.parent.name
        return f"{parent}/{path.name}"

    return path.name


def parse_postprocess_result(status_data: dict) -> PostprocessResult | None:
    """Parse postprocess status data into structured result object.

    Args:
        status_data: Raw postprocess status dictionary

    Returns:
        Structured PostprocessResult or None if data is invalid
    """
    if not status_data or not isinstance(status_data, dict):
        return None

    # Parse steps
    steps_dict = {}
    steps_raw = status_data.get("steps", {})
    if isinstance(steps_raw, dict):
        for step_name, step_data in steps_raw.items():
            if isinstance(step_data, dict):
                steps_dict[step_name] = PostprocessStepResult(
                    status=step_data.get("status", "unknown"),
                    message=step_data.get("message"),
                    paths=step_data.get("paths"),
                    completed_at=step_data.get("completed_at"),
                    reason=step_data.get("reason"),
                    output_file=step_data.get("output_file"),
                    ai_eval_dir=step_data.get("ai_eval_dir"),
                )

    return PostprocessResult(
        success=status_data.get("success", False),
        final_status=status_data.get("final_status"),
        steps=steps_dict if steps_dict else None,
        test_phase=status_data.get("test_phase"),
    )
