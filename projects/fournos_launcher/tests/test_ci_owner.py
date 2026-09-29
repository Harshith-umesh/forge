"""Tests for assigning Fournos jobs to the person who triggered CI."""

import json

import pytest

from projects.core.ci_entrypoint.github.pr_args import PR_TRIGGER_COMMENT_AUTHOR_FILENAME
from projects.fournos_launcher.orchestration import ci


class ConfigStub:
    def __init__(self, values):
        self.values = values

    def get_config(self, key, default=None, **_kwargs):
        return self.values.get(key, default)

    def set_config(self, key, value):
        self.values[key] = value


def test_comment_author_takes_precedence_over_pr_author(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    (metadata_dir / "pull_request.json").write_text(
        json.dumps({"user": {"login": "pr-author"}})
    )
    (metadata_dir / PR_TRIGGER_COMMENT_AUTHOR_FILENAME).write_text("test-commenter")
    project_config = ConfigStub({})
    monkeypatch.setattr(ci.config, "project", project_config)
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)

    ci._set_job_owner_from_trigger()

    assert project_config.values["fournos.job.owner"] == "test-commenter"


def test_pr_author_is_fallback_when_comment_author_is_unavailable(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    (metadata_dir / "pull_request.json").write_text(
        json.dumps({"user": {"login": "pr-author"}})
    )
    project_config = ConfigStub({})
    monkeypatch.setattr(ci.config, "project", project_config)
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)

    ci._set_job_owner_from_trigger()

    assert project_config.values["fournos.job.owner"] == "pr-author"


def test_invalid_pr_metadata_is_not_swallowed(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    (metadata_dir / "pull_request.json").write_text("not-json")
    monkeypatch.setattr(ci.config, "project", ConfigStub({}))
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)

    with pytest.raises(json.JSONDecodeError):
        ci._set_job_owner_from_trigger()


def test_empty_trigger_author_is_not_swallowed(tmp_path, monkeypatch):
    metadata_dir = tmp_path / ci.CI_METADATA_DIRNAME
    metadata_dir.mkdir()
    (metadata_dir / PR_TRIGGER_COMMENT_AUTHOR_FILENAME).write_text("\n")
    monkeypatch.setattr(ci.config, "project", ConfigStub({}))
    monkeypatch.setattr(ci.env, "ARTIFACT_DIR", tmp_path)

    with pytest.raises(ValueError, match="author file is empty"):
        ci._set_job_owner_from_trigger()
