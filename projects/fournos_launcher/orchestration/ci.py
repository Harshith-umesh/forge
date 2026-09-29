#!/usr/bin/env python3
"""
FOURNOS launcher project CI Operations

"""

import logging
import os
import types

import click

from projects.core.ci_entrypoint.github.pr_args import PR_TRIGGER_COMMENT_AUTHOR_FILENAME
from projects.core.ci_entrypoint.prepare_ci import CI_METADATA_DIRNAME
from projects.core.library import ci as ci_lib
from projects.core.library import config, env
from projects.fournos_launcher.orchestration import job_management, utils
from projects.fournos_launcher.orchestration import submit as submit_mod

logger = logging.getLogger(__name__)


def _set_job_owner_from_trigger() -> None:
    """
    Set job owner from the authorized /test commenter.

    The authorized /test comment author's GitHub login is stored beside the
    existing trigger-comment metadata. GitHub PR runs must have this metadata;
    non-PR runs retain the configured owner when it is absent.
    """
    metadata_dir = env.ARTIFACT_DIR / CI_METADATA_DIRNAME
    trigger_author_file = metadata_dir / PR_TRIGGER_COMMENT_AUTHOR_FILENAME
    if not trigger_author_file.exists():
        pull_number = os.environ.get("PULL_NUMBER")
        if pull_number:
            raise FileNotFoundError(
                f"Missing authorized /test commenter metadata for GitHub PR #{pull_number}: "
                f"{trigger_author_file}"
            )
        logger.debug("No /test commenter metadata found outside a GitHub PR")
        return

    trigger_author = trigger_author_file.read_text(encoding="utf-8").strip()
    if not trigger_author:
        raise ValueError(f"Trigger comment author file is empty: {trigger_author_file}")
    config.project.set_config("fournos.job.owner", trigger_author)
    logger.info(f"Set job owner from /test comment: {trigger_author}")


@click.group(cls=ci_lib.HelpfulGroup)
@click.pass_context
@ci_lib.safe_ci_function
def main(ctx):
    """FOURNOS Project launcher CI Operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    submit_mod.init()
    utils.ensure_oc_available()

    # Set job owner from the authorized /test commenter when available.
    _set_job_owner_from_trigger()

    # Set CI job label for tracking and cancellation
    ci_label = job_management.generate_ci_job_label()
    if ci_label:
        config.project.set_config("fournos.job.ci_label", ci_label)
        logger.info(f"Set CI job label: {ci_label}")


@main.command()
@click.pass_context
@ci_lib.safe_ci_entrypoint
def submit(ctx):
    """Submit a CI job to FOURNOS CI entrypoint."""
    return submit_mod.submit_job()


if __name__ == "__main__":
    main()
