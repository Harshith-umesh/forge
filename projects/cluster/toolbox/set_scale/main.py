#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
from datetime import datetime

import yaml

from projects.core.dsl import always, entrypoint, execute_tasks, retry, shell, task

logger = logging.getLogger("DSL")

# Default compute instance types by cloud provider
DEFAULT_COMPUTE_INSTANCES = {
    "aws": "g4dn.2xlarge",
    "ibm": "gx3-16x80x1l4",  # 1x L4 compute
}


@entrypoint
def run(
    replicas: int,
    *,
    instance_type: str = "",
    machineset_name: str = "",
    taint: str = "",
    force: bool = False,
    base_machineset: str = "",
    use_base_name_prefix: bool = False,
    root_disk_size_gib: int = 0,
) -> int:
    """
    Scale cluster with compute nodes, auto-detecting cloud provider and using appropriate compute instance types.

    This command combines machineset creation and scaling operations, automatically detecting
    whether the cluster is running on IBM Cloud or AWS, and using appropriate default compute
    instance types if not specified.

    Args:
        replicas: Number of compute nodes to ensure in the cluster
        instance_type: compute instance type to use (auto-detected if not specified)
        machineset_name: Name for the compute machineset (auto-generated if not specified)
        taint: Taint to apply to compute nodes (format: key=value:effect)
        force: Force scaling down other machinesets of the same instance type
        base_machineset: Base machineset to derive from (auto-selected if not specified)
        use_base_name_prefix: Include base machineset name as prefix in generated machineset name
        root_disk_size_gib: Root disk size in GiB for IBM Cloud instances (0 = use default)
    """

    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    return "Prepared directories for compute scaling operation"


@task
def validate_parameters(args, ctx):
    """Validate command parameters"""

    if args.replicas < 0:
        raise ValueError("replicas must be >= 0")

    if args.taint and ":" not in args.taint:
        raise ValueError("taint must be in format 'key=value:effect'")

    if args.root_disk_size_gib < 0:
        raise ValueError("root_disk_size_gib must be >= 0")

    if args.root_disk_size_gib > 0 and args.root_disk_size_gib < 25:
        raise ValueError("root_disk_size_gib must be >= 25 GiB if specified (IBM Cloud minimum)")

    return f"Validated parameters for {args.replicas} compute nodes"


@task
def detect_cloud_provider(args, ctx):
    """Auto-detect cloud provider (AWS or IBM) based on existing machinesets"""

    # Fallback: try to detect from cluster infrastructure
    infra_result = shell.run(
        "oc get infrastructure cluster -o jsonpath='{.spec.platformSpec.type}'",
        check=False,
    )
    if infra_result.success:
        platform = infra_result.stdout.strip().lower()
        if platform == "aws":
            ctx.cloud_provider = "aws"
        elif platform == "ibmcloud":
            ctx.cloud_provider = "ibm"
        else:
            raise RuntimeError(f"Unsupported cloud platform: {platform}")
    else:
        raise RuntimeError("Could not detect cloud provider from machinesets or infrastructure")

    # Set provider-specific configuration
    ctx.is_ibm = ctx.cloud_provider == "ibm"
    ctx.is_aws = ctx.cloud_provider == "aws"
    ctx.instance_type_field = "profile" if ctx.is_ibm else "instanceType"

    return f"Detected cloud provider: {ctx.cloud_provider}"


@task
def determine_compute_instance_type(args, ctx):
    """Determine compute instance type to use"""

    if args.instance_type:
        ctx.compute_instance_type = args.instance_type
        source = "user-specified"
    else:
        ctx.compute_instance_type = DEFAULT_COMPUTE_INSTANCES[ctx.cloud_provider]
        source = "auto-detected"

    return f"Using compute instance type: {ctx.compute_instance_type} ({source})"


@task
def get_existing_machinesets(args, ctx):
    """Get information about existing machinesets"""

    # Get all worker machinesets
    result = shell.run(
        "oc get machinesets -n openshift-machine-api "
        '-o jsonpath=\'{range .items[?(@.spec.template.metadata.labels.machine\\.openshift\\.io/cluster-api-machine-role=="worker")]}{.metadata.name}{"\\n"}{end}\'',
    )

    if not result.stdout.strip():
        raise RuntimeError("No worker machinesets found in cluster")

    ctx.all_worker_machinesets = result.stdout.strip().split("\n")

    # Check for existing compute machinesets
    compute_check_result = shell.run(
        f"oc get machineset -n openshift-machine-api "
        f'-o jsonpath=\'{{range .items[?(@.spec.template.spec.providerSpec.value.{ctx.instance_type_field}=="{ctx.compute_instance_type}")]}}'
        f'{{.metadata.name}}{{"\\n"}}{{end}}\'',
        check=False,
    )

    ctx.existing_compute_machinesets = (
        compute_check_result.stdout.strip().split("\n")
        if compute_check_result.stdout.strip()
        else []
    )

    return f"Found {len(ctx.all_worker_machinesets)} worker machinesets, {len(ctx.existing_compute_machinesets)} existing compute machinesets"


@task
def determine_base_machineset(args, ctx):
    """Determine which machineset to use as the base for compute machineset creation"""

    if args.base_machineset:
        if args.base_machineset not in ctx.all_worker_machinesets:
            raise ValueError(
                f"Specified base machineset '{args.base_machineset}' does not exist or is not a worker machineset"
            )
        ctx.base_machineset_name = args.base_machineset
        source = "user-specified"
    else:
        ctx.base_machineset_name = ctx.all_worker_machinesets[0]
        source = "auto-selected"

    return f"Using base machineset: {ctx.base_machineset_name} ({source})"


@task
def determine_compute_machineset_name(args, ctx):
    """Determine name for compute machineset"""

    if args.machineset_name:
        ctx.compute_machineset_name = args.machineset_name
        source = "user-specified"
    else:
        # Auto-generate name based on instance type and optionally base machineset
        instance_suffix = ctx.compute_instance_type.replace(".", "-").lower()

        if args.use_base_name_prefix:
            base_name = ctx.base_machineset_name
            ctx.compute_machineset_name = f"{base_name}--{instance_suffix}"
            source = "auto-generated (with base prefix)"
        else:
            ctx.compute_machineset_name = instance_suffix
            source = "auto-generated (instance type only)"

    return f"compute machineset name: {ctx.compute_machineset_name} ({source})"


@task
def create_or_update_compute_machineset(args, ctx):
    """Create or update the compute machineset if it doesn't exist or has wrong configuration"""

    # Check if target machineset exists with correct instance type
    if ctx.compute_machineset_name in [
        ms.split("--")[0] + "--" + ms.split("--")[1] if "--" in ms else ms
        for ms in ctx.existing_compute_machinesets
    ]:
        return f"compute machineset {ctx.compute_machineset_name} already exists with correct instance type"

    # Delete existing machineset with same name but wrong instance type
    shell.run(
        f"oc delete machineset {ctx.compute_machineset_name} -n openshift-machine-api --ignore-not-found",
        check=False,
    )

    # Get base machineset definition
    result = shell.run(
        f"oc get machineset {ctx.base_machineset_name} -n openshift-machine-api -ojson",
        log_stdout=False,
    )

    ctx.base_machineset_json = json.loads(result.stdout)

    # Create new machineset JSON
    ctx.compute_machineset = create_compute_machineset(ctx, args)

    # Save the new machineset to file
    ctx.compute_machineset_file = (
        args.artifact_dir / "src" / f"{ctx.compute_machineset_name}-machineset.yaml"
    )
    with open(ctx.compute_machineset_file, "w") as f:
        yaml.dump(ctx.compute_machineset, f, indent=2)

    # Apply the new machineset
    shell.run(f"oc apply -f {ctx.compute_machineset_file}")

    return f"Created/updated compute machineset {ctx.compute_machineset_name}"


def create_compute_machineset(ctx, args):
    """Create compute machineset based on base machineset"""

    new_machineset = ctx.base_machineset_json.copy()

    # Update metadata
    new_machineset["metadata"]["name"] = ctx.compute_machineset_name
    new_machineset["metadata"].pop("uid", None)
    new_machineset["metadata"].pop("selfLink", None)
    new_machineset["metadata"].pop("resourceVersion", None)
    new_machineset["metadata"].pop("generation", None)
    new_machineset["metadata"].pop("creationTimestamp", None)

    # Update spec
    new_machineset["spec"]["replicas"] = 0  # Start with 0, will scale later
    new_machineset["spec"]["selector"]["matchLabels"][
        "machine.openshift.io/cluster-api-machineset"
    ] = ctx.compute_machineset_name
    new_machineset["spec"]["template"]["metadata"]["labels"][
        "machine.openshift.io/cluster-api-machineset"
    ] = ctx.compute_machineset_name

    # Update instance type
    new_machineset["spec"]["template"]["spec"]["providerSpec"]["value"][ctx.instance_type_field] = (
        ctx.compute_instance_type
    )

    # Update root disk size for IBM Cloud
    if ctx.is_ibm and args.root_disk_size_gib > 0:
        boot_volume = new_machineset["spec"]["template"]["spec"]["providerSpec"]["value"].get(
            "bootVolume", {}
        )
        boot_volume["sizeGiB"] = args.root_disk_size_gib
        new_machineset["spec"]["template"]["spec"]["providerSpec"]["value"]["bootVolume"] = (
            boot_volume
        )

    # Add taints if specified
    if args.taint:
        key, rest = args.taint.split("=", 1)
        value, effect = rest.split(":", 1)

        # Add taint to machine spec
        new_machineset["spec"]["template"]["spec"]["taints"] = [
            {"effect": effect, "key": key, "value": value}
        ]

        # Add labels to template
        new_machineset["spec"]["template"]["metadata"]["labels"][key] = value
        new_machineset["spec"]["template"]["spec"]["metadata"] = {"labels": {key: value}}

    # Remove status
    new_machineset.pop("status", None)

    return new_machineset


@task
def scale_compute_machineset(args, ctx):
    """Scale the compute machineset to the desired number of replicas"""

    # Get current replicas of all compute machinesets
    result = shell.run(
        f"oc get machineset -n openshift-machine-api "
        f'-o jsonpath=\'{{range .items[?(@.spec.template.spec.providerSpec.value.{ctx.instance_type_field}=="{ctx.compute_instance_type}")]}}'
        f'{{.spec.replicas}}{{"\\n"}}{{end}}\'',
    )

    current_replica_counts = [int(x) for x in result.stdout.strip().split("\n") if x]
    current_total = sum(current_replica_counts)

    # Get names of all compute machinesets
    result = shell.run(
        f"oc get machineset -n openshift-machine-api "
        f'-o jsonpath=\'{{range .items[?(@.spec.template.spec.providerSpec.value.{ctx.instance_type_field}=="{ctx.compute_instance_type}")]}}'
        f'{{.metadata.name}}{{"\\n"}}{{end}}\'',
    )

    compute_machinesets = [x for x in result.stdout.strip().split("\n") if x]

    # Set target machineset early so it's available for wait task
    target_machineset = (
        ctx.compute_machineset_name
        if ctx.compute_machineset_name in compute_machinesets
        else compute_machinesets[0]
        if compute_machinesets
        else None
    )

    if target_machineset:
        ctx.target_machineset = target_machineset

    if current_total == args.replicas:
        return f"compute machineset already has {args.replicas} replicas, no scaling needed"

    if not compute_machinesets:
        raise RuntimeError(
            f"No compute machinesets found with instance type {ctx.compute_instance_type}"
        )

    # If we have multiple machinesets and need to scale down, check force flag
    if len(compute_machinesets) > 1 and current_total > args.replicas and not args.force:
        non_first_replicas = sum(current_replica_counts[1:])
        if non_first_replicas > 0:
            raise RuntimeError(
                f"Cannot scale down multiple compute machinesets without force flag. "
                f"Non-primary machinesets have {non_first_replicas} replicas. "
                f"Use --force to scale down all but the first machineset to 0."
            )

    # Scale down all but the first machineset to 0 if force is used
    if args.force and len(compute_machinesets) > 1:
        for machineset_name in compute_machinesets[1:]:
            shell.run(
                f"oc patch machineset -n openshift-machine-api {machineset_name} "
                f'--patch \'{{"spec": {{"replicas": 0}}}}\' --type merge'
            )

    # Scale the target machineset to the desired replicas
    if not target_machineset:
        raise RuntimeError("No target machineset identified for scaling")

    shell.run(
        f"oc patch machineset -n openshift-machine-api {target_machineset} "
        f'--patch \'{{"spec": {{"replicas": {args.replicas}}}}}\' --type merge'
    )

    return f"Scaled {target_machineset} to {args.replicas} replicas"


@retry(attempts=40, delay=30)  # Default 40 attempts * 30s = 20 minutes max wait
@task
def wait_for_compute_nodes_ready(args, ctx):
    """Wait for compute nodes to be ready"""

    if args.replicas == 0:
        return "Scaling to 0 replicas completed - no compute nodes to wait for"

    if not hasattr(ctx, "target_machineset"):
        logger.error("target_machineset not set - scaling may have failed")
        return False  # Retry

    result = shell.run(
        f"oc get machineset {ctx.target_machineset} -n openshift-machine-api",
        check=False,
    )

    # Check machineset readiness
    result = shell.run(
        f"oc get machineset {ctx.target_machineset} -n openshift-machine-api -ojson",
        check=False,
        log_stdout=False,
    )

    if not result.success:
        logger.warning("Failed to query machineset status")
        return False  # Retry

    machineset_status = json.loads(result.stdout)
    status = machineset_status.get("status", {})

    ready_replicas = status.get("readyReplicas", 0)
    available_replicas = status.get("availableReplicas", 0)
    current_replicas = status.get("replicas", 0)

    result = shell.run(
        f"oc get machines -n openshift-machine-api "
        f"-l machine.openshift.io/cluster-api-machineset={ctx.target_machineset} ",
        check=False,
    )

    # Check for failed machines in this machineset
    failed_machines_result = shell.run(
        f"oc get machines -n openshift-machine-api "
        f"-l machine.openshift.io/cluster-api-machineset={ctx.target_machineset} "
        f"-ojson",
        check=False,
        log_stdout=False,
    )

    if failed_machines_result.success:
        machines_data = json.loads(failed_machines_result.stdout)
        failed_machines = []
        failed_details = []

        for machine in machines_data.get("items", []):
            phase = machine.get("status", {}).get("phase", "")
            if phase == "Failed":
                machine_name = machine.get("metadata", {}).get("name", "unknown")
                error_message = machine.get("status", {}).get(
                    "errorMessage", "No error message available"
                )
                failed_machines.append(machine_name)
                failed_details.append({"name": machine_name, "error": error_message})

        if failed_machines:
            # Generate FAILURE file
            failure_content = "# Machine Scaling Failure Report\n\n"
            failure_content += f"**Timestamp:** {datetime.now().isoformat()}\n"
            failure_content += "**Namespace:** openshift-machine-api\n"
            failure_content += f"**Machineset:** {ctx.target_machineset}\n"
            failure_content += f"**Instance Type:** {ctx.compute_instance_type}\n"
            failure_content += f"**Failed Machines:** {len(failed_machines)}\n\n"
            failure_content += "## Failed Machine Details\n\n"

            for detail in failed_details:
                failure_content += f"### {detail['name']}\n"
                failure_content += (
                    f"**Location:** machine/{detail['name']} in namespace/openshift-machine-api\n"
                )
                failure_content += f"**Parent:** machineset/{ctx.target_machineset}\n"
                failure_content += f"**Error:** {detail['error']}\n\n"

            failure_file = args.artifact_dir / "FAILURE.txt"
            with open(failure_file, "w") as f:
                f.write(failure_content)

            logger.error(
                f"Found {len(failed_machines)} failed machines: {', '.join(failed_machines)}"
            )
            for detail in failed_details:
                logger.error(f"  {detail['name']}: {detail['error']}")
            logger.error("Failed machines will prevent the machineset from reaching ready state")
            logger.error("Manual intervention required to resolve failed machines")
            logger.error(f"Detailed failure report saved to: {failure_file}")

            raise RuntimeError(
                f"Scaling failed: {len(failed_machines)} machines in Failed state: {', '.join(failed_machines)}. "
                f"See {failure_file} for detailed error messages and resolution steps."
            )

    if not (
        ready_replicas == args.replicas
        and available_replicas == args.replicas
        and current_replicas == args.replicas
    ):
        logger.info(
            f"compute nodes status: "
            f"{ready_replicas}/{args.replicas} ready, "
            f"{available_replicas}/{args.replicas} available, "
            f"{current_replicas}/{args.replicas} current"
        )
        return False  # Retry

    return f"All {args.replicas} compute nodes are ready"


@always
@task
def capture_compute_machineset_state(args, ctx):
    """Capture final state of compute machinesets"""

    # Capture all compute machinesets
    shell.run(
        "oc get machineset -n openshift-machine-api "
        "-l machine.openshift.io/cluster-api-machine-role=worker "
        "-o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "all-worker-machinesets.yaml",
        check=False,
    )

    # Capture compute-specific machinesets
    if hasattr(ctx, "instance_type_field"):
        shell.run(
            f"oc get machineset -n openshift-machine-api "
            f"-o json | jq '.items[] | select(.spec.template.spec.providerSpec.value.{ctx.instance_type_field}==\"{ctx.compute_instance_type}\")'",
            stdout_dest=args.artifact_dir / "artifacts" / "compute-machinesets.json",
            check=False,
        )

    # Capture machineset descriptions
    if hasattr(ctx, "target_machineset"):
        shell.run(
            f"oc describe machineset {ctx.target_machineset} -n openshift-machine-api",
            stdout_dest=args.artifact_dir
            / "artifacts"
            / f"machineset-{ctx.target_machineset}-describe.txt",
            check=False,
        )

    return "Captured compute machineset state"


@always
@task
def capture_compute_machines_state(args, ctx):
    """Capture state of machines created by compute machinesets"""

    if not hasattr(ctx, "target_machineset"):
        return "No target machineset to capture machines for"

    # Capture machines
    shell.run(
        f"oc get machines -n openshift-machine-api "
        f"-l machine.openshift.io/cluster-api-machineset={ctx.target_machineset} "
        f"-o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / f"machines-{ctx.target_machineset}.yaml",
        check=False,
    )

    shell.run(
        f"oc describe machines -n openshift-machine-api "
        f"-l machine.openshift.io/cluster-api-machineset={ctx.target_machineset}",
        stdout_dest=args.artifact_dir
        / "artifacts"
        / f"machines-{ctx.target_machineset}-describe.txt",
        check=False,
    )

    return "Captured compute machine state"


@always
@task
def capture_compute_nodes_state(args, ctx):
    """Capture state of compute nodes"""

    # Get compute nodes by instance type annotation or label
    shell.run(
        "oc get nodes -o wide",
        stdout_dest=args.artifact_dir / "artifacts" / "all-nodes.status",
        check=False,
    )

    # Try to identify compute nodes and capture their details
    if hasattr(ctx, "compute_instance_type"):
        shell.run(
            f'oc get nodes -o json | jq -r \'.items[] | select(.metadata.annotations."machine.openshift.io/instance-type"=="{ctx.compute_instance_type}" or .metadata.labels."node.kubernetes.io/instance-type"=="{ctx.compute_instance_type}") | .metadata.name\'',
            stdout_dest=args.artifact_dir / "artifacts" / "compute-node-names.txt",
            check=False,
        )

    return "Captured compute node state"


if __name__ == "__main__":
    run.main()
