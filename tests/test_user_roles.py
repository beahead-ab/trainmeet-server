"""Three-role permission matrix, real HTTP isolation and legacy SQLite migration."""
import ast
import inspect
import json
from pathlib import Path
import sqlite3
import textwrap
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
import unittest

import test_admin_users as fixtures
from tmbox_gateway.http_server import HTTPAPIError, TrainMeetRequestHandler
from tmbox_gateway.identity import AdminAccessError, AdminUserPermissionError, IdentityStore

ROLES = ("owner", "admin", "user")


class RoleMatrixTests(unittest.TestCase):
    _invite = fixtures.OwnerGateTests._invite
    _as = fixtures.OwnerGateTests._as

    def setUp(self):
        fixtures.OwnerGateTests.setUp(self)
        self._invite("driver", "driver-password", "user")
        self.actors = dict(zip(ROLES, (self._as("casper"), self._as("lars"), self._as("driver"))))
        self.sequence = 0

    def target(self, role):
        self.sequence += 1
        return self.identities.invite_admin_user("Target", f"target{self.sequence}@example.se", role)

    def snapshot(self):
        return [self.identities._connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("admin_users", "admin_sessions")]

    def check(self, allowed, operation):
        before = self.snapshot()
        if allowed:
            return operation()
        with self.assertRaises(HTTPAPIError) as caught:
            operation()
        self.assertEqual(403, caught.exception.status)
        self.assertEqual(before, self.snapshot(), "forbidden request changed stored account data")

    def test_invitation_matrix(self):
        for actor_role, actor in self.actors.items():
            for role in ROLES:
                with self.subTest(actor=actor_role, invited=role):
                    self.sequence += 1
                    result = self.check(actor_role == "owner" or (actor_role == "admin" and role != "owner"),
                        lambda: self.application.create_admin_user(actor, {
                            "display_name": "Invited", "email": f"invite{self.sequence}@example.se", "role": role}))
                    if result:
                        self.assertEqual(role, result["user"]["role"])
                        self.assertTrue(result["user"]["can_use_driver"])
                        self.identities.redeem_admin_setup(result["user"]["email"], result["user"]["setup_code"], "invited-password")
                        token = self.identities.create_admin_session(result["user"]["email"], "invited-password")
                        self.assertEqual(role, self.identities.admin_session_user(token)["role"])

    def test_edit_delete_and_reissue_matrix(self):
        for actor_role, actor in self.actors.items():
            for target_role in ROLES:
                allowed = actor_role == "owner" or (actor_role == "admin" and target_role != "owner")
                for action in ("edit", "delete", "reissue"):
                    with self.subTest(actor=actor_role, target=target_role, action=action):
                        target = self.target(target_role)
                        payload = {"user_id": target["user_id"]}
                        if action == "edit":
                            payload.update(display_name="Changed", email=f"changed{self.sequence}@example.se", password="new-password")
                            operation = self.application.update_admin_user
                        elif action == "delete":
                            operation = self.application.delete_admin_user
                        else:
                            operation = self.application.reissue_admin_setup
                        self.check(allowed, lambda: operation(actor, payload))

    def test_role_change_matrix_including_combined_edit(self):
        for actor_role, actor in self.actors.items():
            for target_role in ROLES:
                for new_role in ROLES:
                    with self.subTest(actor=actor_role, target=target_role, assigned=new_role):
                        target = self.target(target_role)
                        allowed = actor_role == "owner" or (actor_role == "admin" and "owner" not in (target_role, new_role))
                        result = self.check(allowed, lambda: self.application.update_admin_user(actor, {
                            "user_id": target["user_id"], "role": new_role, "display_name": "Changed"}))
                        if result:
                            self.assertEqual(new_role, result["user"]["role"])

    def test_self_profile_and_forbidden_self_actions(self):
        for role, actor in self.actors.items():
            with self.subTest(role=role):
                self.assertEqual("My name", self.application.own_account(actor, {"display_name": "My name"})["user"]["display_name"])
                for field in ({"role": role}, {"user_id": actor.admin_user_id}, {"can_use_driver": False}):
                    self.check(False, lambda: self.application.own_account(actor, field))
                for new_role in set(ROLES) - {role}:
                    with self.assertRaises(HTTPAPIError) as caught:
                        self.application.update_admin_user(actor, {"user_id": actor.admin_user_id, "role": new_role})
                    self.assertEqual(403 if role == "user" or new_role == "owner" and role == "admin" else 409, caught.exception.status)
                with self.assertRaises(HTTPAPIError) as caught:
                    self.application.delete_admin_user(actor, {"user_id": actor.admin_user_id})
                self.assertEqual(403 if role == "user" else 409, caught.exception.status)

    def test_own_profile_validation_is_atomic_and_password_revokes_sessions(self):
        actor = self.actors["user"]
        before = self.snapshot()
        with self.assertRaises(HTTPAPIError):
            self.application.own_account(actor, {"display_name": "Unsaved", "email": "casper@example.se"})
        self.assertEqual(before, self.snapshot())
        token = self.identities.create_admin_session("driver@example.se", "driver-password")
        self.application.own_account(actor, {"display_name": "Driver", "email": "driver.new@example.se", "password": ""})
        self.assertIsNotNone(self.identities.admin_session_user(token))
        self.application.own_account(actor, {"password": "replacement-password"})
        self.assertIsNone(self.identities.admin_session_user(token))
        self.assertIsNotNone(self.identities.create_admin_session("driver.new@example.se", "replacement-password"))

    def test_mutation_authorization_is_inside_write_transaction_and_reads_current_roles(self):
        original = self.identities._authorize_user_change_locked
        def checked(*args, **kwargs):
            self.assertTrue(self.identities._connection.in_transaction)
            return original(*args, **kwargs)
        with patch.object(self.identities, "_authorize_user_change_locked", side_effect=checked):
            self.application.create_admin_user(self.actors["admin"], {"display_name": "New", "email": "new@example.se", "role": "user"})
            target = self.target("user")
            self.application.reissue_admin_setup(self.actors["admin"], {"user_id": target["user_id"]})
            self.application.update_admin_user(self.actors["admin"], {"user_id": target["user_id"], "role": "admin"})
            self.application.delete_admin_user(self.actors["admin"], {"user_id": target["user_id"]})
        # Change roles on a separate database connection, after creating the request actor.
        with sqlite3.connect(self.identities.path) as other:
            other.execute("UPDATE admin_users SET role = 'user' WHERE user_id = ?", (self.actors["admin"].admin_user_id,))
        self.check(False, lambda: self.application.create_admin_user(self.actors["admin"], {"display_name": "Denied", "email": "denied@example.se"}))
        # Store-level checks must also reject a stale actor after an earlier HTTP check.
        with self.assertRaises(AdminUserPermissionError):
            self.identities.invite_admin_user("Denied", "denied@example.se", actor=self.actors["admin"])
        target = self.target("owner")
        for operation in (self.identities.delete_admin_user, self.identities.reissue_admin_setup):
            with self.assertRaises(AdminUserPermissionError):
                operation(target["user_id"], actor=self.actors["admin"])


class DriverHTTPTests(unittest.TestCase):
    _sign_in = fixtures.UserRoutesOverHTTPTests._sign_in
    def setUp(self):
        fixtures.UserRoutesOverHTTPTests.setUp(self)
        invited = self.identities.invite_admin_user("Driver", "driver@example.se", "user")
        self.identities.redeem_admin_setup("driver@example.se", invited["setup_code"], "driver-password")
        self.cookie = "trainmeet_admin=" + self.identities.create_admin_session("driver@example.se", "driver-password")
        self.driver = invited

    def request(self, path, method="GET", data=None):
        request = Request(self.base + path, headers={"Cookie": self.cookie, "Content-Type": "application/json"},
                          method=method, data=json.dumps(data or {}).encode() if method == "POST" else None)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_all_declared_private_api_routes_reject_user_session(self):
        # Enumerate actual handler routes so a newly added admin/traffic API
        # cannot silently inherit WEB_ADMIN rights through this account.
        public = {"/v1/auth/status", "/v1/workspaces", "/v1/display", "/v1/about", "/v1/events", "/v1/setup", "/v1/info", "/v1/account"}
        for method in ("GET", "POST"):
            source = ast.parse(textwrap.dedent(inspect.getsource(getattr(TrainMeetRequestHandler, "do_" + method))))
            paths = {node.value for node in ast.walk(source) if isinstance(node, ast.Constant)
                     and isinstance(node.value, str) and node.value.startswith("/v1/")}
            for path in sorted(paths):
                if path.startswith("/v1/driver/"):
                    continue
                if method == "GET" and (path in public or path.startswith("/v1/clock-faces/")):
                    continue
                if method == "POST" and path in {"/v1/account", "/v1/auth/login", "/v1/auth/logout"}:
                    continue
                with self.subTest(method=method, path=path):
                    self.assertEqual(403, self.request(path, method)[0])

    def test_profile_public_feeds_and_live_role_revocation(self):
        status, payload = self.request("/v1/auth/status")
        self.assertEqual((200, "user", True), (status, payload["role"], payload["can_use_driver"]))
        self.assertEqual(self.driver["user_id"], self.request("/v1/account")[1]["user"]["user_id"])
        self.assertEqual(200, self.request("/v1/account", "POST", {"display_name": "Me"})[0])
        self.assertEqual(403, self.request("/v1/account", "POST", {"role": "owner"})[0])
        for path in ("/v1/workspaces", "/v1/display", "/v1/about"):
            self.assertEqual(200, self.request(path)[0])
        self.identities.set_admin_user_role(self.driver["user_id"], "admin")
        self.assertEqual(200, self.request("/v1/admin/users")[0])
        self.identities.set_admin_user_role(self.driver["user_id"], "user")
        self.assertEqual(403, self.request("/v1/admin/users")[0])


class LegacyRoleMigrationTests(unittest.TestCase):
    def test_rebuild_preserves_accounts_credentials_codes_indexes_and_sessions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            store = IdentityStore(path)
            owner = store.create_first_owner("Owner", "owner@example.se", "owner-password")
            admin = store.invite_admin_user("Admin", "admin@example.se", "admin")
            pending = store.invite_admin_user("Pending", "pending@example.se", "owner")
            store.redeem_admin_setup(admin["email"], admin["setup_code"], "admin-password")
            token = store.create_admin_session(owner["email"], "owner-password")
            reset = store.issue_admin_password_reset(admin["email"])
            accounts = store._connection.execute("SELECT * FROM admin_users ORDER BY user_id").fetchall()
            sessions = store._connection.execute("SELECT * FROM admin_sessions").fetchall()
            store.close()
            # Reproduce the released schema, with all populated columns intact.
            with sqlite3.connect(path) as db:
                schema = db.execute("SELECT sql FROM sqlite_master WHERE name='admin_users'").fetchone()[0]
                db.execute(schema.replace("admin_users", "old_users", 1).replace("'owner', 'admin', 'user'", "'owner', 'admin'"))
                db.execute("INSERT INTO old_users SELECT * FROM admin_users")
                db.execute("DROP TABLE admin_users")
                db.execute("ALTER TABLE old_users RENAME TO admin_users")
                db.execute("CREATE UNIQUE INDEX admin_users_email ON admin_users(email) WHERE email IS NOT NULL")
                db.execute("CREATE INDEX custom_user_name ON admin_users(display_name)")
            # A failure after copying/dropping must restore the original table.
            connect = sqlite3.connect
            connections = []
            def fail_rename(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK)
                connections.append(connection)
                return connection
            with patch("tmbox_gateway.identity.sqlite3.connect", side_effect=fail_rename):
                with self.assertRaises(sqlite3.DatabaseError):
                    IdentityStore(path)
            for connection in connections:
                connection.close()
            with connect(path) as db:
                self.assertEqual(accounts, db.execute("SELECT * FROM admin_users ORDER BY user_id").fetchall())
                self.assertNotIn("'user'", db.execute("SELECT sql FROM sqlite_master WHERE name='admin_users'").fetchone()[0])
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='admin_users_rebuild'").fetchone())
            reopened = IdentityStore(path)
            self.addCleanup(reopened.close)
            self.assertEqual(accounts, reopened._connection.execute("SELECT * FROM admin_users ORDER BY user_id").fetchall())
            self.assertEqual(sessions, reopened._connection.execute("SELECT * FROM admin_sessions").fetchall())
            self.assertIsNotNone(reopened.admin_session_user(token))
            self.assertIsNotNone(reopened.create_admin_session(admin["email"], "admin-password"))
            self.assertIn("custom_user_name", {row[1] for row in reopened._connection.execute("PRAGMA index_list(admin_users)")})
            with self.assertRaises(AdminAccessError):
                reopened.invite_admin_user("Duplicate", admin["email"], "user")
            with self.assertRaises(sqlite3.IntegrityError):
                reopened._connection.execute("UPDATE admin_users SET role = 'invalid'")
            reopened.redeem_admin_setup(pending["email"], pending["setup_code"], "pending-password")
            reopened.redeem_admin_setup(admin["email"], reset[1], "reset-password")
            reopened.invite_admin_user("Driver", "driver@example.se", "user")
            second = IdentityStore(path)
            self.addCleanup(second.close)
            self.assertEqual(reopened.list_admin_users(), second.list_admin_users())
            self.assertEqual([], second._connection.execute("PRAGMA foreign_key_check").fetchall())
            self.assertEqual("ok", second._connection.execute("PRAGMA integrity_check").fetchone()[0])
