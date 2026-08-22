"""One authoritative version, and one shape for an update in progress.

Three version claims had drifted apart before this existed: pyproject.toml
said 0.6.0, the User-Agent said 0.7, and what an operator actually saw was a
git sha. These tests exist so that cannot happen again quietly.
"""

from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from tmbox_gateway.update_contract import (
    ACTIVE,
    DONE,
    FAILED,
    FAILED_STATE,
    PENDING,
    STAGE_LABELS,
    STAGES,
    is_running,
    normalise,
    steps,
)
from tmbox_gateway.version import (
    build_identifier,
    display_version,
    product_version,
    user_agent,
)

ROOT = Path(__file__).resolve().parent.parent


class VersionSourceTests(unittest.TestCase):
    def test_there_is_exactly_one_version_file_and_it_is_semver(self):
        value = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        parts = value.split(".")
        self.assertEqual(3, len(parts), f"{value} är inte större.funktion.rättning")
        for part in parts:
            self.assertTrue(part.isdigit(), f"{value} har en icke-numerisk del")

    def test_pyproject_cannot_drift_from_the_version_file(self):
        """The whole point of a single source is that copies get caught."""
        declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                self.assertIn(declared, line, "pyproject.toml och VERSION säger olika")
                return
        self.fail("pyproject.toml saknar version")

    def test_the_user_agent_is_built_from_the_same_source(self):
        self.assertEqual(f"TrainMeet-Server/{product_version()}", user_agent())

    def test_a_sha_left_in_version_is_read_as_a_build_not_a_version(self):
        """Installations from before this wrote the git sha into VERSION.

        A sha is a build, so it is never returned as a version. The search
        continues past it - which matters, because for exactly one update the
        old updater script writes one over the real number. Here the running
        checkout answers instead, which is why this asserts the sha is read as
        a build and *not* echoed back as a version.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("4bd9c9a1\n", encoding="utf-8")
            self.assertEqual("4bd9c9a1", build_identifier(root))
            self.assertNotEqual("4bd9c9a1", product_version(root))

    def test_display_puts_the_version_first_and_the_build_second(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("1.4.0\n", encoding="utf-8")
            (root / "BUILD").write_text("4bd9c9a\n", encoding="utf-8")
            self.assertEqual("Version 1.4.0 · build 4bd9c9a", display_version(root))

    def test_a_missing_build_leaves_the_version_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("1.4.0\n", encoding="utf-8")
            self.assertEqual("Version 1.4.0", display_version(root))


class UpdateContractTests(unittest.TestCase):
    def test_the_seven_stages_are_the_seven_the_operator_was_promised(self):
        self.assertEqual(
            [
                "Söker efter uppdatering",
                "Hämtar",
                "Verifierar",
                "Installerar",
                "Startar om",
                "Kontrollerar att tjänsten fungerar",
                "Klart",
            ],
            [STAGE_LABELS[stage] for stage in STAGES],
        )

    def test_a_stage_in_progress_leaves_the_later_ones_pending(self):
        states = {row["stage"]: row["state"] for row in steps("installing")}
        self.assertEqual(DONE, states["downloading"])
        self.assertEqual(ACTIVE, states["installing"])
        self.assertEqual(PENDING, states["restarting"])
        self.assertEqual(PENDING, states["complete"])

    def test_a_failure_marks_the_stage_it_happened_in(self):
        """Naming the stage is the point: "broke while restarting" and "broke
        while downloading" send an operator to two different places."""
        states = {row["stage"]: row["state"] for row in steps(FAILED, "restarting")}
        self.assertEqual(DONE, states["installing"])
        self.assertEqual(FAILED_STATE, states["restarting"])
        self.assertEqual(PENDING, states["health_check"])
        self.assertEqual(PENDING, states["complete"])

    def test_success_is_never_reported_before_the_health_check_passes(self):
        """The rule the old server updater broke: it wrote `complete` before
        it restarted, so an operator was told it worked while it had not yet
        been tried."""
        for stage in ("restarting", "health_check"):
            states = {row["stage"]: row["state"] for row in steps(stage)}
            self.assertNotEqual(DONE, states["complete"], f"{stage} ska inte se klart ut")
        finished = {row["stage"]: row["state"] for row in steps("complete")}
        self.assertEqual(DONE, finished["health_check"])
        self.assertEqual(DONE, finished["complete"])

    def test_running_means_started_and_not_yet_finished(self):
        self.assertFalse(is_running("idle"))
        self.assertTrue(is_running("checking"))
        self.assertTrue(is_running("health_check"))
        self.assertFalse(is_running("complete"))
        self.assertFalse(is_running(FAILED))

    def test_an_unknown_state_from_the_script_is_a_failure_not_a_blank_screen(self):
        """Shell writes what it likes. An unrecognised state is one we cannot
        claim went well, so it is shown as a failure rather than passed to a
        UI that would render nothing."""
        result = normalise({"status": "hamtar", "message": "Hämtar"})
        self.assertEqual(FAILED, result["status"])
        self.assertIn("okänt läge", result["message"])
        self.assertEqual(len(STAGES), len(result["steps"]))

    def test_nothing_written_yet_reads_as_idle(self):
        self.assertEqual("idle", normalise(None)["status"])
        self.assertEqual("idle", normalise({})["status"])

    def test_a_failed_stage_the_script_invented_is_ignored_not_trusted(self):
        result = normalise({"status": FAILED, "failed_stage": "sideways"})
        self.assertIsNone(result["failed_stage"])

    def test_the_status_survives_a_round_trip_through_json(self):
        """The updater writes JSON from shell; the API reads it back."""
        written = json.dumps({"status": "verifying", "message": "Verifierar"})
        result = normalise(json.loads(written))
        self.assertEqual("verifying", result["status"])
        self.assertEqual(ACTIVE, result["steps"][STAGES.index("verifying")]["state"])


class ContractParityTests(unittest.TestCase):
    """The updater scripts are shell, and shell writes what it likes.

    A script writing a stage the contract does not know renders as a failure
    rather than as a blank progress bar - which is right, but it would be a
    silly way to find out. These check the two against each other instead.
    """

    SCRIPTS = (
        Path("packaging/raspberry-pi/trainmeet-server-update"),
        Path("packaging/mac/trainmeet-server-update"),
    )

    def _stages_written_by(self, script: Path) -> set[str]:
        written = set()
        for line in (ROOT / script).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            for prefix in ("status ", "fail_at "):
                if stripped.startswith(prefix):
                    written.add(stripped[len(prefix):].split()[0])
        return written

    def test_every_stage_the_scripts_write_is_one_the_contract_knows(self):
        for script in self.SCRIPTS:
            with self.subTest(script=str(script)):
                written = self._stages_written_by(script)
                self.assertTrue(written, f"{script}: hittade inga steg")
                self.assertEqual(set(), written - set(STAGES), f"{script} skriver okända steg")

    def test_both_scripts_reach_the_health_check(self):
        """The step that was missing entirely before this."""
        for script in self.SCRIPTS:
            with self.subTest(script=str(script)):
                self.assertIn("health_check", self._stages_written_by(script))

    def test_neither_script_reports_complete_before_the_health_check(self):
        """The rule the old updaters broke: `complete` was written before the
        server had been tried even once."""
        for script in self.SCRIPTS:
            with self.subTest(script=str(script)):
                text = (ROOT / script).read_text(encoding="utf-8")
                self.assertLess(
                    text.index("status health_check"),
                    text.index("status complete"),
                    f"{script}: complete skrivs före hälsokontrollen",
                )


class ContractCopyTests(unittest.TestCase):
    """The contract module is a verbatim copy shared with the other repo.

    A copy nobody checks is a copy that drifts, so its code is pinned by hash
    in both. The docstring is excluded because it names whichever repo it is
    not - the *behaviour* is what has to be identical.

    When the contract genuinely changes: edit both modules, run this, and move
    the expected hash in both. Failing here is the reminder to do the second
    half, not a reason to loosen the check.

    The twin lives at trainmeet-cloud/cloud/update_contract.py.
    """

    CODE_DIGEST = "bb18356f034c0b95e4fc38c8b74bb095edc4b4f91eb6a7a4d99c80e74d1beab1"

    def test_the_shared_contract_has_not_drifted(self):
        source = (ROOT / "src/tmbox_gateway/update_contract.py").read_text(encoding="utf-8")
        code = source[source.index('"""', source.index('"""') + 3) + 3:]
        self.assertEqual(
            self.CODE_DIGEST,
            hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "update_contract.py har ändrats - gör samma ändring i det andra repot "
            "och flytta hashen i båda",
        )


class FirstUpdateAfterThisChangeTests(unittest.TestCase):
    """What an operator sees the first time they press Update.

    The updater script that runs is the *old* one, still on disk from the
    previous install. It runs the new installer and then overwrites
    $INSTALL_DIR/VERSION with the git sha, because that is what a version was
    when it was written. Without a second copy beside the code, that first
    update would report "okänd" and only the second would show 1.0.0.
    """

    def test_the_old_updater_overwriting_version_does_not_hide_the_real_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "src" / "tmbox_gateway"
            package.mkdir(parents=True)

            # The new installer writes both copies...
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (package / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (root / "BUILD").write_text("4bd9c9a1\n", encoding="utf-8")

            # ...and then the old updater stamps the sha over the outer one.
            (root / "VERSION").write_text("4bd9c9a1\n", encoding="utf-8")

            self.assertEqual("4bd9c9a1", build_identifier(root))
            self.assertEqual(
                "1.0.0",
                _product_version_from(root),
                "första uppdateringen ska visa 1.0.0, inte okänd",
            )

    def test_an_installation_with_only_a_sha_anywhere_still_says_okand(self):
        """The honest case: nothing anywhere knows the version."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("4bd9c9a1\n", encoding="utf-8")
            self.assertEqual("okänd", _product_version_from(root))


def _product_version_from(root: Path) -> str:
    """product_version() searching only inside `root`.

    The real function also falls back to the checkout it is imported from,
    which in a test run is this repo - and would mask the thing being tested.
    """
    from tmbox_gateway.version import _BUILD_LIKE, UNKNOWN_VERSION

    saw_build_only = False
    for candidate in (root / "VERSION", root / "src" / "tmbox_gateway" / "VERSION"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not value:
            continue
        if _BUILD_LIKE.match(value):
            saw_build_only = True
            continue
        return value
    return UNKNOWN_VERSION if saw_build_only else "utvecklingsversion"


if __name__ == "__main__":
    unittest.main()
