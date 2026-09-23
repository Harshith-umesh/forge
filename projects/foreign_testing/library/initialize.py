import logging
import os
import pathlib
import shlex
import tempfile

from projects.core.library import run

logger = logging.getLogger(__name__)

REPOSITORY_PATH_ENV = "FORGE_FOREIGN_TESTING_REPO_PATH"
PULL_SHA_ENV = "FORGE_FOREIGN_TESTING_PULL_PULL_SHA"


def get_repository_path():
    repository_path = os.environ.get(REPOSITORY_PATH_ENV)
    if not repository_path:
        raise ValueError(f"{REPOSITORY_PATH_ENV} must be set")

    path = pathlib.Path(repository_path)
    if not path.is_dir():
        raise FileNotFoundError(f"Foreign repository path does not exist: {path}")

    return path


def clone_repository(*, repo_owner, repo_name, pull_pull_sha):
    values = {
        "REPO_OWNER": repo_owner,
        "REPO_NAME": repo_name,
        PULL_SHA_ENV: pull_pull_sha,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"Required foreign repository variables are missing: {', '.join(missing)}")

    repository_path = pathlib.Path(tempfile.mkdtemp(prefix="forge-foreign-testing-"))
    quoted_path = shlex.quote(str(repository_path))
    repository_url = shlex.quote(f"https://github.com/{repo_owner}/{repo_name}")
    commit = shlex.quote(pull_pull_sha)

    run.run(f"git clone {repository_url} {quoted_path}")
    run.run(f"git -C {quoted_path} fetch --quiet origin {commit}")
    run.run(f"git -C {quoted_path} reset --hard FETCH_HEAD")

    os.environ[REPOSITORY_PATH_ENV] = str(repository_path)
    logger.info("Foreign repository checked out at %s", repository_path)
    return repository_path


def initialize():
    if os.environ.get(REPOSITORY_PATH_ENV):
        return get_repository_path()

    return clone_repository(
        repo_owner=os.environ.get("REPO_OWNER"),
        repo_name=os.environ.get("REPO_NAME"),
        pull_pull_sha=os.environ.get(PULL_SHA_ENV),
    )
