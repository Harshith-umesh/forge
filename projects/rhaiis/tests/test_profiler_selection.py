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


def test_profile5_is_not_warmed_up_when_profiler_is_requested() -> None:
    assert _warmup_workload_keys(["profile1", "profile5"], profiler_requested=True) == ["profile1"]


def test_warmup_selection_is_unchanged_when_profiler_is_not_requested() -> None:
    assert _warmup_workload_keys(["profile1", "profile5"], profiler_requested=False) == [
        "profile1",
        "profile5",
    ]
