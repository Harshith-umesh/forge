from __future__ import annotations

from projects.caliper.engine.kpi.format import transform_kpis_to_hierarchical_format


def test_hierarchical_format_merges_common_labels_from_all_kpis():
    kpis = [
        {
            "run_id": "run-1",
            "kpi_id": "generic",
            "value": 1,
            "labels": {"model": "llama"},
        },
        {
            "run_id": "run-1",
            "kpi_id": "dashboard",
            "value": 2,
            "labels": {"model": "llama", "tensor_parallel_size": "2"},
        },
    ]
    model = type("Model", (), {"plugin_module": "projects.caliper.tests.stub_plugin"})()

    output = transform_kpis_to_hierarchical_format(kpis, model)

    assert output["tests"][0]["labels"] == {
        "model": "llama",
        "tensor_parallel_size": "2",
    }
