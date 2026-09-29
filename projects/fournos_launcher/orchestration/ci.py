#!/usr/bin/env python3
"""
FOURNOS launcher project CI Operations

"""

import json
import logging
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
    Set job owner from the /test commenter, falling back to the PR author.

    The authorized /test comment author's GitHub login is stored beside the
    existing trigger-comment metadata. Older runs without that file use
    pull_request.json's author.
    """
    metadata_dir = env.ARTIFACT_DIR / CI_METADATA_DIRNAME
    trigger_author_file = metadata_dir / PR_TRIGGER_COMMENT_AUTHOR_FILENAME
    if trigger_author_file.exists():
        trigger_author = trigger_author_file.read_text(encoding="utf-8").strip()
        if not trigger_author:
            raise ValueError(f"Trigger comment author file is empty: {trigger_author_file}")
        config.project.set_config("fournos.job.owner", trigger_author)
        logger.info(f"Set job owner from /test comment: {trigger_author}")
        return

    pull_request_file = metadata_dir / "pull_request.json"

    if not pull_request_file.exists():
        logger.debug("No pull request metadata found")
        return

    with open(pull_request_file) as f:
        pr_data = json.load(f)

    user_login = pr_data.get("user", {}).get("login")
    if not user_login:
        raise ValueError(f"No user.login found in pull request metadata: {pull_request_file}")

    # Set the job owner
    config.project.set_config("fournos.job.owner", user_login)
    logger.info(f"Set job owner from pull request: {user_login}")


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
