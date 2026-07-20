"""Shared nightly pipeline handlers.

Architecture:
    Core (this package) defines abstract interfaces and the orchestration logic.
    Each project implements concrete receivers/verifiers in its resolvers/ package
    and exposes them via get_receiver() and get_verifier() factory functions.

    The core handlers import the project's resolvers module at runtime and call
    those factories — pure polymorphism, no knowledge of concrete implementations.

Interfaces:
    - ImageReceiver: fetches the latest version from a registry
    - NightlyVerifier: checks if a version was already tested

Handlers:
    - receive_image.run(): calls project's get_receiver().get_latest_version()
    - confirm.run(): calls project's get_verifier().get_last_tested_version()

Projects register these as phases in ci_base:
    "receive-image"  -> projects.core.nightly.receive_image:run
    "confirm-nightly" -> projects.core.nightly.confirm:run
"""
