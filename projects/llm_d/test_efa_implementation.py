#!/usr/bin/env python3
"""
Test script to verify EFA configuration is properly applied to PD deployments.
"""

import sys
import tempfile
from pathlib import Path

import yaml

from projects.llm_d.orchestration.render_inference_service import (
    render_inference_service_from_parts,
)


def test_efa_configuration():
    """Test that EFA configuration is applied when enabled."""

    # Create a temporary config directory with our test manifest
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create manifest directory and EFA config
        manifest_dir = temp_path / "manifests"
        manifest_dir.mkdir()

        efa_config = {
            "init_container": {
                "name": "inject-libfabric",
                "imagePullPolicy": "Always",
                "command": [
                    "/bin/bash",
                    "/artifacts/inject.sh",
                    "/target/plugins",
                    "/target/efa-libs",
                ],
                "volume_mounts": [
                    {"name": "nixl-plugins", "mountPath": "/target/plugins"},
                    {"name": "efa-libs", "mountPath": "/target/efa-libs"},
                ],
            },
            "volumes": [
                {"name": "nixl-plugins", "emptyDir": {}},
                {"name": "efa-libs", "emptyDir": {}},
            ],
            "volume_mounts": [
                {"name": "nixl-plugins", "mountPath": "/opt/nixl-plugins"},
                {"name": "efa-libs", "mountPath": "/opt/efa-libs"},
            ],
            "env": [
                {"name": "FI_PROVIDER", "value": "efa"},
                {"name": "FI_EFA_USE_DEVICE_RDMA", "value": "1"},
                {"name": "NIXL_PLUGIN_DIR", "value": "/opt/nixl-plugins"},
                {
                    "name": "LD_LIBRARY_PATH",
                    "value": "/opt/efa-libs:/usr/local/cuda/lib64:/opt/app-root/lib/python3.12/site-packages/.nixl_cu13.mesonpy.libs",
                },
            ],
            "resources": {"vpc.amazonaws.com/efa": "8"},  # Will be overridden to 4 × TP = 4
        }

        with open(manifest_dir / "pd-efa-config.yaml", "w") as f:
            yaml.dump(efa_config, f)

        # Create template manifest
        template_manifest = {
            "apiVersion": "serving.kserve.io/v1alpha1",
            "kind": "LLMInferenceService",
            "metadata": {"name": "test", "namespace": "test"},
            "spec": {
                "model": {"uri": "", "name": ""},
                "router": {"scheduler": {}, "route": {}, "gateway": {}},
            },
        }

        with open(manifest_dir / "llminferenceservice.yaml", "w") as f:
            yaml.dump(template_manifest, f)

        # Test configuration
        inference_service = {"name": "test", "template": "manifests/llminferenceservice.yaml"}
        deployment_profile = {
            "prefill": {"tensor_parallelism": 1, "replicas": 1},
            "decode": {"tensor_parallelism": 1, "replicas": 1},
            "scheduler": {},
            "pd_config": {},
        }
        model_cache = {"enabled": False}

        # Mock the config access
        import projects.core.library.config as config_module

        # Create a mock config that simulates EFA enabled
        class MockConfig:
            def get_config(self, path, default_value=None, warn=True, print=True):
                config_map = {
                    "deployments.pd.efa.enabled": True,
                    "deployments.pd.efa": {
                        "image": "quay.io/rajjoshi/libfabric-addon:3.5.0-1784900545",
                        "manifest": "manifests/pd-efa-config.yaml",
                        "vllm_extra": {
                            "kv_transfer_config": {
                                "kv_connector_extra_config": {"backends": ["LIBFABRIC"]}
                            }
                        },
                    },
                    "deployments.defaults.labels": {},
                    "deployments.pd.vllm_extra": {
                        "args": ["--block-size", "128"],
                        "env": [],
                        "kv_transfer_config": {
                            "kv_connector": "NixlConnector",
                            "kv_role": "kv_both",
                        },
                    },
                    "deployments.pd.resources": {"rdma/ib": "1"},
                    "deployments.pd.shmem.size": "16Gi",
                    "deployments.defaults.serving_image": None,
                }
                return config_map.get(path, default_value)

        # Mock the config.project
        original_config = getattr(config_module, "project", None)
        config_module.project = MockConfig()

        try:
            # Render the manifest
            result = render_inference_service_from_parts(
                config_dir=temp_path,
                namespace="test-ns",
                inference_service=inference_service,
                model_name="test-model",
                model_slug="test-model",
                deployment_profile=deployment_profile,
                model_cache=model_cache,
                deployment_profile_name="test-pd",
            )

            print("✓ Successfully rendered manifest with EFA configuration")

            # Verify EFA configuration is applied
            prefill_template = result["spec"]["prefill"]["template"]
            decode_template = result["spec"]["template"]

            # Check init containers
            assert "initContainers" in prefill_template, "Prefill template missing initContainers"
            assert "initContainers" in decode_template, "Decode template missing initContainers"

            # Check EFA init container exists
            prefill_init = prefill_template["initContainers"][0]
            decode_init = decode_template["initContainers"][0]

            assert prefill_init["name"] == "inject-libfabric", (
                "Wrong init container name in prefill"
            )
            assert decode_init["name"] == "inject-libfabric", "Wrong init container name in decode"
            assert prefill_init["image"] == "quay.io/rajjoshi/libfabric-addon:3.5.0-1784900545", (
                "Wrong image in prefill"
            )

            # Check volumes
            assert "volumes" in prefill_template, "Prefill template missing volumes"
            assert "volumes" in decode_template, "Decode template missing volumes"

            # Check volume mounts on main container
            prefill_main = prefill_template["containers"][0]
            decode_main = decode_template["containers"][0]

            assert "volumeMounts" in prefill_main, "Prefill main container missing volumeMounts"
            assert "volumeMounts" in decode_main, "Decode main container missing volumeMounts"

            # Check EFA resources
            prefill_resources = prefill_main["resources"]
            decode_resources = decode_main["resources"]

            assert "vpc.amazonaws.com/efa" in prefill_resources["requests"], (
                "Prefill missing EFA resource in requests"
            )
            assert "vpc.amazonaws.com/efa" in prefill_resources["limits"], (
                "Prefill missing EFA resource in limits"
            )
            assert "vpc.amazonaws.com/efa" in decode_resources["requests"], (
                "Decode missing EFA resource in requests"
            )
            assert "vpc.amazonaws.com/efa" in decode_resources["limits"], (
                "Decode missing EFA resource in limits"
            )

            # Check kv_transfer_config in VLLM_ADDITIONAL_ARGS
            prefill_env = {env["name"]: env["value"] for env in prefill_main.get("env", [])}
            decode_env = {env["name"]: env["value"] for env in decode_main.get("env", [])}

            assert "VLLM_ADDITIONAL_ARGS" in prefill_env, "Prefill missing VLLM_ADDITIONAL_ARGS"
            assert "VLLM_ADDITIONAL_ARGS" in decode_env, "Decode missing VLLM_ADDITIONAL_ARGS"

            prefill_args = prefill_env["VLLM_ADDITIONAL_ARGS"]
            decode_args = decode_env["VLLM_ADDITIONAL_ARGS"]

            # Check that kv_transfer_config is present and properly formatted
            expected_kv_config = '{"kv_connector":"NixlConnector","kv_role":"kv_both","kv_connector_extra_config":{"backends":["LIBFABRIC"]}}'
            expected_arg = f"--kv-transfer-config '{expected_kv_config}'"

            assert expected_arg in prefill_args, (
                f"Prefill missing expected kv_transfer_config. Got: {prefill_args}"
            )
            assert expected_arg in decode_args, (
                f"Decode missing expected kv_transfer_config. Got: {decode_args}"
            )

            # Check that --block-size 128 is also present
            assert "--block-size 128" in prefill_args, "Prefill missing --block-size 128"
            assert "--block-size 128" in decode_args, "Decode missing --block-size 128"

            # Check security context for IPC_LOCK capability
            assert "securityContext" in prefill_main, (
                "Prefill main container missing securityContext"
            )
            assert "securityContext" in decode_main, "Decode main container missing securityContext"

            prefill_security = prefill_main["securityContext"]
            decode_security = decode_main["securityContext"]

            assert "capabilities" in prefill_security, (
                "Prefill securityContext missing capabilities"
            )
            assert "capabilities" in decode_security, "Decode securityContext missing capabilities"

            assert "add" in prefill_security["capabilities"], "Prefill capabilities missing add"
            assert "add" in decode_security["capabilities"], "Decode capabilities missing add"

            assert "IPC_LOCK" in prefill_security["capabilities"]["add"], (
                "Prefill missing IPC_LOCK capability"
            )
            assert "IPC_LOCK" in decode_security["capabilities"]["add"], (
                "Decode missing IPC_LOCK capability"
            )

            # Check shared memory volume configuration
            prefill_volumes = {vol["name"]: vol for vol in prefill_template.get("volumes", [])}
            decode_volumes = {vol["name"]: vol for vol in decode_template.get("volumes", [])}

            assert "shm" in prefill_volumes, "Prefill template missing shm volume"
            assert "shm" in decode_volumes, "Decode template missing shm volume"

            # Check shm volume configuration
            prefill_shm = prefill_volumes["shm"]
            decode_shm = decode_volumes["shm"]

            assert "emptyDir" in prefill_shm, "Prefill shm volume missing emptyDir"
            assert "emptyDir" in decode_shm, "Decode shm volume missing emptyDir"

            assert prefill_shm["emptyDir"]["medium"] == "Memory", "Prefill shm volume wrong medium"
            assert prefill_shm["emptyDir"]["sizeLimit"] == "16Gi", "Prefill shm volume wrong size"
            assert decode_shm["emptyDir"]["medium"] == "Memory", "Decode shm volume wrong medium"
            assert decode_shm["emptyDir"]["sizeLimit"] == "16Gi", "Decode shm volume wrong size"

            # Check shm volume mounts
            prefill_mounts = {
                mount["name"]: mount for mount in prefill_main.get("volumeMounts", [])
            }
            decode_mounts = {mount["name"]: mount for mount in decode_main.get("volumeMounts", [])}

            assert "shm" in prefill_mounts, "Prefill main container missing shm volume mount"
            assert "shm" in decode_mounts, "Decode main container missing shm volume mount"

            assert prefill_mounts["shm"]["mountPath"] == "/dev/shm", "Prefill shm wrong mount path"
            assert decode_mounts["shm"]["mountPath"] == "/dev/shm", "Decode shm wrong mount path"

            print("✓ All EFA configuration checks passed")
            print("✓ kv_transfer_config properly merged and formatted")
            print("✓ Security context with IPC_LOCK capability applied")
            print("✓ Shared memory volume and mount configured correctly")
            print("✓ Test completed successfully!")

            return True

        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False
        finally:
            # Restore original config
            config_module.project = original_config


if __name__ == "__main__":
    success = test_efa_configuration()
    sys.exit(0 if success else 1)
