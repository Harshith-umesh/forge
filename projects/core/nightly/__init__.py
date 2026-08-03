"""Shared nightly pipeline handlers.

Architecture:
    Core (this package) defines abstract interfaces and the orchestration logic.
    Each project implements concrete receivers/verifiers in its nightly_resolvers/ package
    and exposes them via get_receiver() and get_verifier() factory functions.

    The core handler imports the project's nightly_resolvers module at runtime and calls
    those factories — pure polymorphism, no knowledge of concrete implementations.

Interfaces:
    - ImageReceiver: fetches the latest version from a registry
    - NightlyVerifier: checks if a version was already tested

Handler:
    - handler.run(): single phase that resolves version, compares, and creates FournosJob

Projects register this as a phase in ci_base:
    "nightly" -> projects.core.nightly.handler:run
"""
