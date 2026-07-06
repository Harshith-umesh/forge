from __future__ import annotations

from projects.cluster.toolbox.cleanup_operators import main as cleanup_operators


def test_subscription_owner_refs_from_installplans() -> None:
    installplans = {
        "items": [
            {
                "metadata": {
                    "ownerReferences": [
                        {"kind": "Subscription", "name": "authorino-operator"},
                        {"kind": "Subscription", "name": "dns-operator"},
                        {"kind": "ClusterServiceVersion", "name": "ignored-csv"},
                        {"kind": "Subscription", "name": "authorino-operator"},
                    ]
                }
            }
        ]
    }

    assert cleanup_operators._subscription_owner_refs_from_installplans(installplans) == [
        "authorino-operator",
        "dns-operator",
    ]


def test_expand_operators_with_installplan_owners(monkeypatch) -> None:
    discovered_owners = {
        ("rhods-operator", "redhat-ods-operator"): [
            "authorino-operator-stable-redhat-operators-openshift-marketplace",
            "dns-operator-stable-redhat-operators-openshift-marketplace",
            "limitador-operator-stable-redhat-operators-openshift-marketplace",
            "rhcl-operator",
            "rhods-operator",
        ],
        (
            "authorino-operator-stable-redhat-operators-openshift-marketplace",
            "redhat-ods-operator",
        ): ["rhods-operator"],
    }

    def fake_discover(subscription_name: str, namespace: str) -> list[str]:
        return discovered_owners.get((subscription_name, namespace), [])

    monkeypatch.setattr(
        cleanup_operators,
        "_discover_installplan_subscription_owners",
        fake_discover,
    )

    assert cleanup_operators._expand_operators_with_installplan_owners(
        [
            ("rhods-operator", "redhat-ods-operator"),
            ("rhcl-operator", "openshift-operators"),
        ]
    ) == [
        ("rhods-operator", "redhat-ods-operator"),
        ("rhcl-operator", "openshift-operators"),
        (
            "authorino-operator-stable-redhat-operators-openshift-marketplace",
            "redhat-ods-operator",
        ),
        (
            "dns-operator-stable-redhat-operators-openshift-marketplace",
            "redhat-ods-operator",
        ),
        (
            "limitador-operator-stable-redhat-operators-openshift-marketplace",
            "redhat-ods-operator",
        ),
        ("rhcl-operator", "redhat-ods-operator"),
    ]
