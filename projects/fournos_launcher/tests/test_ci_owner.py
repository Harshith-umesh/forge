"""Tests for assigning Fournos jobs to the person who triggered CI."""

import pytest

from projects.core.ci_entrypoint.github.pr_args import PR_TRIGGER_COMMENT_AUTHOR_FILENAME
from projects.fournos_launcher.orchestration import ci


class ConfigStub:
    def __init__(self, values):
        self.values = values

    def set_config(self, key, value):
        self.values[key] = value


def test_comment_author_is_assigned_as_job_owner(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    (metadata_dir / PR_TRIGGER_COMMENT_AUTHOR_FILENAME).write_text("test-commenter")
    project_config = ConfigStub({})
    monkeypatch.setattr(ci.config, "project", project_config)
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)

    ci._set_job_owner_from_trigger()

    assert project_config.values["fournos.job.owner"] == "test-commenter"


def test_missing_trigger_author_fails_for_github_pr(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    monkeypatch.setattr(ci.config, "project", ConfigStub({}))
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setenv("PULL_NUMBER", "123")

    with pytest.raises(FileNotFoundError, match="GitHub PR #123"):
        ci._set_job_owner_from_trigger()


def test_empty_trigger_author_is_not_swallowed(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    (metadata_dir / PR_TRIGGER_COMMENT_AUTHOR_FILENAME).write_text("\n")
    monkeypatch.setattr(ci.config, "project", ConfigStub({}))
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)

    with pytest.raises(ValueError, match="author file is empty"):
        ci._set_job_owner_from_trigger()


def test_missing_trigger_author_is_allowed_outside_github_pr(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    project_config = ConfigStub({"fournos.job.owner": "configured-owner"})
    monkeypatch.setattr(ci.config, "project", project_config)
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)
    monkeypatch.delenv("PULL_NUMBER", raising=False)

    ci._set_job_owner_from_trigger()

    assert project_config.values["fournos.job.owner"] == "configured-owner"
