"""First-run onboarding wizard for Meridian (round 13).

Packages the manual sequence (configure API key -> create project -> import
first document -> run bootstrap LLM sweep -> see first results) into a
single guided ``meridian init`` command. Idempotent and resumable: state is
persisted to ``<projects_dir>/_meridian/onboarding_state.json`` after every
step so a half-finished run can be picked up where it left off.

Public surface intentionally small — the wizard composes existing CLI
surfaces (``create_project``, ``ingest_file``, ``run_bootstrap_sweep``)
rather than duplicating their logic. See module docstrings inside
``wizard.py`` for the per-step UX rules.
"""

from meridian.onboarding.wizard import (
    STEP_NAMES,
    OnboardingState,
    load_state,
    run_step,
    run_wizard,
    save_state,
    state_path,
)

__all__ = [
    "OnboardingState",
    "STEP_NAMES",
    "load_state",
    "run_step",
    "run_wizard",
    "save_state",
    "state_path",
]
