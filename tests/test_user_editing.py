"""Whole-form edits obey role boundaries and either commit or change nothing."""
from dataclasses import replace
from unittest.mock import Mock

import unittest
import test_admin_users as fixtures
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import AdminAccessError


class UserEditingTests(unittest.TestCase):
    _invite = fixtures.OwnerGateTests._invite
    _as = fixtures.OwnerGateTests._as

    # Reuse the real identity/application fixture without duplicating its tests.
    def setUp(self):
        fixtures.OwnerGateTests.setUp(self)
        self.benny = self._invite("benny", "bennys-losenord")
        self.owner = self._as("casper")
        self.admin = self._as("lars")
        self.application._record_user_change = Mock()

    def edit(self, actor, target, **changes):
        return self.application.update_admin_user(actor, {"user_id": target["user_id"], **changes})["user"]

    def snapshot(self):
        # Include credentials, codes and session tokens, not just public fields.
        return [self.identities._connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("admin_users", "admin_sessions")]

    def assert_rejected_unchanged(self, actor, target, status, **changes):
        before = self.snapshot()
        self.application._record_user_change.reset_mock()
        with self.assertRaises(HTTPAPIError) as caught:
            self.edit(actor, target, **changes)
        self.assertEqual(status, int(caught.exception.status))
        self.assertEqual(before, self.snapshot())
        self.application._record_user_change.assert_not_called()

    def test_admin_can_edit_another_admin_in_one_save(self):
        old_session = self.identities.create_admin_session("benny@example.se", "bennys-losenord")
        user = self.edit(self.admin, self.benny, display_name="Benny i Lekby",
                         email="Benny.Ny@Example.se", role="admin", password="bennys-nya-losenord")
        self.assertEqual(("Benny i Lekby", "benny.ny@example.se", "admin"),
                         (user["display_name"], user["email"], user["role"]))
        self.assertIsNone(self.identities.admin_session_user(str(old_session)))
        self.assertIsNone(self.identities.create_admin_session("benny.ny@example.se", "bennys-losenord"))
        self.assertIsNotNone(self.identities.create_admin_session("benny.ny@example.se", "bennys-nya-losenord"))
        self.assertNotIn("bennys-nya-losenord", str(self.application._record_user_change.mock_calls))
        self.assertNotIn("password_digest", user)

    def test_owner_can_edit_another_owner_and_change_their_role(self):
        self.identities.set_admin_user_role(str(self.benny["user_id"]), "owner")
        user = self.edit(self.owner, self.benny, display_name="Ny ägare", email="ny@example.se",
                         password="ett-nytt-losenord", role="admin")
        self.assertEqual(("Ny ägare", "ny@example.se", "admin"),
                         (user["display_name"], user["email"], user["role"]))

    def test_owner_can_promote_an_admin(self):
        self.assertEqual("owner", self.edit(self.owner, self.benny, role="owner")["role"])

    def test_admin_cannot_edit_owner_fields_individually_or_together(self):
        owner = self.identities.admin_user(self.owner.admin_user_id)
        changes = {"display_name": "Kapad", "email": "kapad@example.se", "role": "admin",
                   "password": "kapat-losenord"}
        for change in [*({key: value} for key, value in changes.items()), changes]:
            with self.subTest(change=change):
                self.assert_rejected_unchanged(self.admin, owner, 403, **change)

    def test_admin_cannot_promote_self_or_someone_else_even_with_other_changes(self):
        for target in (self.lars, self.benny):
            with self.subTest(target=target["user_id"]):
                self.assert_rejected_unchanged(self.admin, target, 403, role="owner",
                                               display_name="Ska inte sparas", email="osparad@example.se",
                                               password="inte-heller-sparat")

    def test_future_unprivileged_role_cannot_edit_admins_or_owners_or_itself(self):
        # No new account role is introduced here. Unknown/future actors fail closed.
        ordinary = replace(self.admin, admin_role="user", admin_user_id=None)
        for target in (self.lars, self.benny, self.identities.admin_user(self.owner.admin_user_id)):
            self.assert_rejected_unchanged(ordinary, target, 403, role="owner", display_name="Stopp")

    def test_failed_validation_rolls_back_earlier_fields_and_role(self):
        self.identities.create_admin_session("benny@example.se", "bennys-losenord")
        for invalid in ({"email": "casper@example.se"}, {"email": "fel-adress"},
                        {"role": "okänd"}, {"password": "kort"}):
            changes = {"display_name": "Ska rullas tillbaka", "email": "ny@example.se", "role": "owner"}
            changes.update(invalid)
            with self.subTest(invalid=invalid):
                self.assert_rejected_unchanged(self.owner, self.benny, 400, **changes)

    def test_empty_password_keeps_password_and_sessions(self):
        token = self.identities.create_admin_session("benny@example.se", "bennys-losenord")
        self.edit(self.admin, self.benny, display_name="Benny Ny", password="")
        self.assertIsNotNone(self.identities.admin_session_user(str(token)))
        self.assertIsNotNone(self.identities.create_admin_session("benny@example.se", "bennys-losenord"))

    def test_password_change_invalidates_outstanding_invitation_and_reset(self):
        invited = self.identities.invite_admin_user("Väntande", "vantar@example.se")
        self.edit(self.admin, invited, password="ett-satt-losenord")
        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("vantar@example.se", invited["setup_code"], "kapat-losenord")
        reset = self.identities.issue_admin_password_reset("benny@example.se")
        self.edit(self.admin, self.benny, password="ett-nytt-losenord")
        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("benny@example.se", reset[1], "kapat-losenord")

    def test_email_change_invalidates_code_sent_to_the_old_address(self):
        reset = self.identities.issue_admin_password_reset("benny@example.se")
        self.edit(self.admin, self.benny, email="ny@example.se")
        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("ny@example.se", reset[1], "kapat-losenord")

    def test_roles_are_rechecked_after_the_request_client_was_created(self):
        self.identities.set_admin_user_role(str(self.benny["user_id"]), "owner")
        self.assert_rejected_unchanged(self.admin, self.benny, 403, display_name="Stopp")
        stale_owner = self.owner
        self.identities.set_admin_user_role(str(self.owner.admin_user_id), "admin")
        self.assert_rejected_unchanged(stale_owner, self.benny, 403, display_name="Stopp")

    def test_owner_cannot_demote_self_with_other_changes(self):
        self.identities.set_admin_user_role(str(self.benny["user_id"]), "owner")
        self.assert_rejected_unchanged(self.owner, self.identities.admin_user(self.owner.admin_user_id),
                                       409, role="admin", display_name="Stopp")

    def test_last_owner_guard_applies_to_combined_edits(self):
        console = replace(self.owner, admin_user_id=None)
        self.assert_rejected_unchanged(console, self.identities.admin_user(self.owner.admin_user_id),
                                       400, role="admin", display_name="Stopp")

    def test_unknown_target_and_removed_actor_cannot_edit(self):
        self.assert_rejected_unchanged(self.owner, {"user_id": "missing"}, 400, display_name="Stopp")
        self.identities.delete_admin_user(str(self.lars["user_id"]))
        self.assert_rejected_unchanged(self.admin, self.benny, 403, display_name="Stopp")
