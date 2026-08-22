"""The shared shape of an update in progress.

TrainMeet Server and TrainMeet Cloud update by completely different means -
a systemd unit unpacking a tarball on a Raspberry Pi, and a host service
rebuilding a Docker image - but an operator watching either one is asking the
same question: how far has it got, and did it work?

So the *stages* are shared even though nothing else is. This module is
duplicated verbatim in trainmeet-cloud as `cloud/update_contract.py`, and a
test in each repo pins the vocabulary so the two cannot drift apart in
silence. It is deliberately dependency-free so copying it stays cheap.

The order matters twice over: it is the order they happen in, and it is the
order the progress bar draws them.
"""

from __future__ import annotations

from typing import Any

#: The stages, in order. `idle` and `failed` are states, not stages, and are
#: deliberately outside this list.
STAGES: tuple[str, ...] = (
    "checking",
    "downloading",
    "verifying",
    "installing",
    "restarting",
    "health_check",
    "complete",
)

#: What each stage is called for a person. Swedish, because the admin is.
STAGE_LABELS: dict[str, str] = {
    "checking": "Söker efter uppdatering",
    "downloading": "Hämtar",
    "verifying": "Verifierar",
    "installing": "Installerar",
    "restarting": "Startar om",
    "health_check": "Kontrollerar att tjänsten fungerar",
    "complete": "Klart",
}

IDLE = "idle"
FAILED = "failed"

#: Every value `status` may hold.
STATUSES: tuple[str, ...] = (IDLE, *STAGES, FAILED)

#: A stage that has not been reached yet, is running, has passed, or broke.
PENDING, ACTIVE, DONE, FAILED_STATE = "pending", "active", "done", "failed"


def is_running(status: str) -> bool:
    """True while an update is under way and neither finished nor failed."""
    return status in STAGES and status != "complete"


def steps(status: str, failed_stage: str | None = None) -> list[dict[str, str]]:
    """The stage list a progress bar draws, each with its state.

    A failure marks the stage it happened in and leaves every later stage
    pending - the point of naming the stage is that "it broke while
    restarting" and "it broke while downloading" send an operator to two
    different places.
    """
    if status == FAILED and failed_stage in STAGES:
        reached = STAGES.index(failed_stage)
    elif status in STAGES:
        reached = STAGES.index(status)
    else:
        reached = -1

    result: list[dict[str, str]] = []
    for index, stage in enumerate(STAGES):
        if status == FAILED and index == reached:
            state = FAILED_STATE
        elif index < reached or (status == "complete" and stage == "complete"):
            state = DONE
        elif index == reached:
            state = ACTIVE
        else:
            state = PENDING
        result.append({"stage": stage, "label": STAGE_LABELS[stage], "state": state})
    return result


def normalise(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Take whatever the updater wrote and make it answer the contract.

    Updater scripts are shell, and shell writes what it likes. Anything that
    is not a status we know becomes `failed` rather than being passed through
    to a UI that would render nothing: an unrecognised state is a state we
    cannot claim went well.
    """
    value = raw if isinstance(raw, dict) else {}
    status = str(value.get("status") or IDLE)
    if status not in STATUSES:
        return {
            "status": FAILED,
            "failed_stage": None,
            "message": f"Uppdateringstjänsten svarade med ett okänt läge: {status}",
            "steps": steps(FAILED),
        }
    failed_stage = value.get("failed_stage")
    failed_stage = str(failed_stage) if failed_stage in STAGES else None
    return {
        "status": status,
        "failed_stage": failed_stage,
        "message": str(value.get("message") or STAGE_LABELS.get(status, "")),
        "steps": steps(status, failed_stage),
    }
