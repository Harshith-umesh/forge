import logging

logger = logging.getLogger(__name__)


def get_client(server, token):
    """Create a Jira client using a Personal Access Token.

    Args:
        server: Jira server URL (e.g. https://issues.redhat.com)
        token: Personal Access Token

    Returns:
        Jira client instance
    """
    from atlassian import Jira

    return Jira(url=server, token=token)


def find_or_create_ticket(client, project_key, pr_number, pr_title):
    """Find an existing ticket for this PR or create a new one.

    Args:
        client: Jira client
        project_key: Jira project key (e.g. PSAP-CI)
        pr_number: GitHub PR number
        pr_title: GitHub PR title (used when creating)

    Returns:
        str: Jira ticket key (e.g. PSAP-CI-42)
    """
    jql = f'project = "{project_key}" AND summary ~ "PR #{pr_number}"'
    logger.info(f"Searching Jira with JQL: {jql}")

    results = client.post(
        "rest/api/3/search/jql",
        data={"jql": jql, "fields": ["key", "summary"]},
    )
    issues = results.get("issues", [])

    if issues:
        ticket_key = issues[0]["key"]
        logger.info(f"Found existing Jira ticket: {ticket_key}")
        return ticket_key

    summary = f"[Forge] PR #{pr_number}: {pr_title}"
    logger.info(f"Creating new Jira ticket in {project_key}: {summary}")

    result = client.issue_create(
        fields={
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": "Task"},
        }
    )

    ticket_key = result["key"]
    logger.info(f"Created Jira ticket: {ticket_key}")

    return ticket_key


def markdown_to_adf(markdown_text):
    """Convert GitHub-flavored Markdown to Atlassian Document Format.

    Args:
        markdown_text: Markdown string

    Returns:
        dict: ADF document ({"version": 1, "type": "doc", "content": [...]})
    """
    from marklassian import markdown_to_adf as _markdown_to_adf

    return _markdown_to_adf(markdown_text)


def post_comment(client, ticket_key, markdown_content, dry_run=False):
    """Post a comment in ADF format to a Jira ticket.

    Args:
        client: Jira client
        ticket_key: Jira ticket key (e.g. PSAP-CI-42)
        markdown_content: Markdown string to convert and post
        dry_run: If True, log but don't post

    Returns:
        bool: True if successful
    """
    adf_body = markdown_to_adf(markdown_content)

    if dry_run:
        logger.info(f"DRY RUN: Would post ADF comment to {ticket_key}")
        return True

    logger.info(f"Posting ADF comment to {ticket_key}")
    response = client.post(
        f"rest/api/3/issue/{ticket_key}/comment",
        data={"body": adf_body},
    )

    if response and not isinstance(response, dict):
        logger.error(f"Failed to post comment to {ticket_key}: {response}")
        return False

    logger.info(f"Successfully posted comment to {ticket_key}")
    return True


def send_jira_notification(
    server,
    token,
    project_key,
    pr_number,
    pr_title,
    markdown_content,
    extra_tickets=None,
    dry_run=False,
):
    """Send a notification to Jira: find/create a ticket and post a comment.

    Args:
        server: the Jira server address
        token: the client token to use
        project_key: Jira project key (e.g. PSAP-CI)
        pr_number: GitHub PR number
        pr_title: GitHub PR title
        markdown_content: Markdown notification content
        extra_tickets: Optional str or list of additional ticket keys to post to
        dry_run: If True, log but don't post

    Returns:
        bool: True if all notifications succeeded
    """

    if not server or not token:
        raise ValueError("Server or token missing")

    client = get_client(server, token)
    success = True

    ticket_key = find_or_create_ticket(client, project_key, pr_number, pr_title)
    if not ticket_key:
        logger.error("Failed to find or create Jira ticket")
        return False

    if not post_comment(client, ticket_key, markdown_content, dry_run=dry_run):
        success = False

    if extra_tickets:
        if isinstance(extra_tickets, str):
            extra_tickets = [extra_tickets]

        for extra_key in extra_tickets:
            logger.info(f"Posting to extra ticket: {extra_key}")
            if not post_comment(client, extra_key, markdown_content, dry_run=dry_run):
                logger.warning(f"Failed to post comment to extra ticket {extra_key}")
                success = False

    return success
