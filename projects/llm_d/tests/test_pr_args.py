from __future__ import annotations

import pytest

from projects.llm_d.orchestration import pr_args


def test_parse_project_directives_does_not_override_framework_test_preset_handling() -> None:
    overrides, directives = pr_args.parse_project_directives("/test fournos llm_d smoke")

    assert overrides == {}
    assert directives == []


def test_parse_project_directives_ignores_test_without_preset() -> None:
    overrides, directives = pr_args.parse_project_directives("/test fournos llm_d")

    assert overrides == {}
    assert directives == []


def test_parse_project_directives_ignores_other_projects() -> None:
    overrides, directives = pr_args.parse_project_directives("/test fournos skeleton smoke")

    assert overrides == {}
    assert directives == []


def test_rhoai_rc_image_sets_custom_catalog_config() -> None:
    image = "quay.io/rhoai/rhoai-fbc-fragment@sha256:e213152dc5bdaaf5724d269484914f3e1d7b4a4959d4405cb3eca9dd6297b310"
    overrides, directives = pr_args.parse_project_directives(f"/rhoai.rc-image {image}")

    assert overrides == {
        "platform.rhoai.custom_catalog.enabled": True,
        "platform.rhoai.custom_catalog.image": image,
        "platform.operators.rhods-operator.channel": "beta",
    }
    assert len(directives) == 1


def test_rhoai_rc_image_without_image_raises() -> None:
    with pytest.raises(ValueError, match="image cannot be empty"):
        pr_args.parse_project_directives("/rhoai.rc-image")


def test_rhoai_rc_image_with_other_directives() -> None:
    comment = """/test fournos llm_d smoke
/rhoai.rc-image quay.io/rhoai/rhoai-fbc-fragment@sha256:abc123"""

    overrides, directives = pr_args.parse_project_directives(comment)

    assert overrides["platform.rhoai.custom_catalog.enabled"] is True
    assert (
        overrides["platform.rhoai.custom_catalog.image"]
        == "quay.io/rhoai/rhoai-fbc-fragment@sha256:abc123"
    )
    assert overrides["platform.operators.rhods-operator.channel"] == "beta"
    assert len(directives) == 1
