from projects.rhaiis.orchestration.test_phase import (
    _profiler_workload_keys,
    _warmup_workload_keys,
)


def test_profile5_is_excluded_but_other_workloads_remain_eligible() -> None:
    assert _profiler_workload_keys(["profile1", "profile5", "profile7"]) == [
        "profile1",
        "profile7",
    ]


def test_profile5_only_has_no_profiler_workload() -> None:
    assert _profiler_workload_keys(["profile5"]) == []


def test_profile5_is_never_warmed_up() -> None:
    assert _warmup_workload_keys(["profile1", "profile5"]) == ["profile1"]


def test_profile5_only_is_not_warmed_up() -> None:
    assert _warmup_workload_keys(["profile5"]) == []


def test_other_workloads_remain_eligible_for_warmup() -> None:
    assert _warmup_workload_keys(["profile1", "profile5", "profile7"]) == [
        "profile1",
        "profile7",
    ]
