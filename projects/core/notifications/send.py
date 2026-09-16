import logging
import os

import projects.core.notifications.github.api as github_api
from projects.core.library import vault as vault_lib

logger = logging.getLogger(__name__)


GITHUB_APP_PEM_FILE = "topsail-bot.2024-09-18.private-key.pem"
GITHUB_APP_CLIENT_ID_FILE = "topsail-bot.clientid"
SLACK_TOKEN_FILE = "topsail-bot.slack-token"

DEFAULT_REPO_OWNER = "openshift-psap"
DEFAULT_REPO_NAME = "forge"

#  avoid importing projects.caliper.orchestration.postprocess here
POSTPROCESS_STATUS_FILENAME = "postprocess_status.yaml"


def send_notification(
    message,
    github=True,
    jira=False,
    dry_run=False,
    notification_vault=None,
):
    """Send a generic notification message to GitHub, Slack, and/or Jira.

    Args:
        message: The notification message content
        github: Whether to send to GitHub (default True)
        jira: Whether to send to Jira (default False, requires notifications.jira config)
        dry_run: Whether to only log the message without sending (default False)
        notification_vault: Optional vault name to get notification secrets from

    Returns:
        bool: False if any notification failed, True if all succeeded
    """

    if not github_api:
        logger.info("Github API not available, don't send notification to github")
        github = False

    if os.environ.get("JOB_TYPE") == "periodic":
        logger.info("Running from a Periodic job, don't send notification to github")
        github = False

    vault_def = vault_lib.get_vault_manager().get_vault(notification_vault)
    if not vault_def:
        if github:
            logger.error(
                f"Cannot send GitHub notification: vault '{notification_vault}' not available"
            )
        if jira:
            logger.error(
                f"Cannot send Jira notification: vault '{notification_vault}' not available"
            )

        if not dry_run:
            return False

    failed = False
    if github and not send_notification_to_github(
        vault_def,
        message,
        dry_run,
    ):
        failed = True

    if jira and not _send_notification_to_jira(
        vault_def,
        message,
        dry_run,
    ):
        failed = True

    return not failed


def _send_notification_to_jira(vault_def, message, dry_run):
    """Send a notification to Jira if configured in the project config.

    Reads notifications.jira config to determine project_key and extra_tickets.
    Creates or finds a ticket matching the PR number, then posts the message
    as an ADF comment.
    """
    try:
        from projects.core.notifications.jira import api as jira_api

        server_file = vault_def.content.get("jira-server")
        token_file = vault_def.content.get("jira-token")

        if not (
            server_file.file_path
            and server_file.file_path.exists()
            and token_file.file_path
            and token_file.file_path.exists()
        ):
            logger.error(
                f"jira-server or jira-token files not available in the '{vault_def.name}' vault"
            )
            return False

        server = server_file.file_path.read_text().strip()
        token = token_file.file_path.read_text().strip()

        # should be retrieved from the configuration
        pr_number = "1234"
        pr_title = "dummy project"
        project_key = "PSAPCI"
        extra_tickets = []

        return jira_api.send_jira_notification(
            server=server,
            token=token,
            pr_number=pr_number,
            pr_title=pr_title,
            project_key=project_key,
            markdown_content=message,
            extra_tickets=extra_tickets,
            dry_run=dry_run,
        )

    except Exception as e:
        logger.error(f"Failed to send Jira notification: {e}")
        return False


###


def send_notification_to_github(vault_def, message, dry_run):
    """Send a generic notification message to GitHub."""

    pem_file, client_id = get_github_secrets(vault_def)
    pr_number = os.environ.get("PULL_NUMBER")
    org, repo = get_org_repo()

    abort = False

    if None in (pem_file, client_id):
        logger.error("github: Cannot access the Github notification secrets")
        abort = True

    if None in (pr_number,):
        logger.error("github: Cannot figure out the PR number")
        abort = True

    if None in (org, repo):
        logger.error("github: Cannot access the org/repo")
        abort = True

    if abort:
        logger.error("github: Aborting due to previous error(s).")
        return False

    user_token = github_api.get_user_token(pem_file, client_id, org, repo)
    if not user_token:
        logger.error("github: Couldn't fetch the user token. Is the app installed in the repo?")
        return False

    if dry_run:
        logger.info(f"Github notification:\n{message}")
        logger.info("***")
        logger.info("***")
        logger.info("***\n")

        return True

    resp = github_api.send_notification(org, repo, user_token, pr_number, message)

    if not resp.ok:
        logger.fatal(f"Github notification post failed :/ {resp.text}")

    return resp.ok


def get_org_repo():
    return (
        os.environ.get("REPO_OWNER", DEFAULT_REPO_OWNER),
        os.environ.get("REPO_NAME", DEFAULT_REPO_NAME),
    )


def get_github_secrets(vault_def):
    pem_file = vault_def.content.get(GITHUB_APP_PEM_FILE).file_path
    client_id_file = vault_def.content.get(GITHUB_APP_CLIENT_ID_FILE).file_path

    if not pem_file:
        logger.warning(
            f"Github App private key does not exists ({GITHUB_APP_PEM_FILE}) in {vault_def.name}"
        )

    if not client_id_file:
        logger.warning(
            f"Github App clientid file does not exists ({GITHUB_APP_CLIENT_ID_FILE}) in {vault_def.name}"
        )

    if not (pem_file and client_id_file):
        return None, None

    client_id_content = client_id_file.read_text().strip()

    return pem_file, client_id_content
