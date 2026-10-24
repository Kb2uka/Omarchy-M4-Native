# SPDX-License-Identifier: MIT
"""First-test permission retains real registration and protection lifecycles."""
import json
import unittest
from unittest.mock import patch

import test_j715_audio_registration as registration_tests
import test_j715_audio_startup as startup_tests
import test_j715_native_package as package_tests
import test_m4_j715_audio_ready as readiness_tests
from test_j715_first_qualification import AuthorizedFixture, authorize, repin, reselect

r, s, registration = readiness_tests.r, readiness_tests.s, registration_tests.registration


class FirstStartupTests(AuthorizedFixture):
    def test_startup_and_direct_gate_do_not_admit_ordinary_session(self):
        began = self.now
        code, _, error = self.cli(publish=True)
        self.assertEqual(code, 0, error)
        self.assertGreaterEqual(self.now - began, 15_000_000_000)
        self.assertEqual(s.main(["--require-registration"], self.root), 0)
        began = self.now
        self.assertEqual(self.cli("gate")[0], 0)
        self.assertGreaterEqual(self.now - began, 1_000_000_000)
        self.assertEqual(s.main([], self.root), 1)
        self.assertFalse(s.artifacts(self.root)[2]["hardware_qualified"])
        self.assertEqual(s.trusted_json(self.root, s.READY)[0]["status"], "ready")

    def test_natural_expiry_blocks_inflight_publication(self):
        self.authorization["expires_monotonic_ns"] = self.now + 1_000_000_000
        repin(self, s.FIRST_AUTHORIZATION, json.dumps(self.authorization).encode())
        reselect(self)
        began = self.now
        self.assertEqual(self.cli(publish=True)[0], 1)
        self.assertGreaterEqual(self.now - began, 15_000_000_000)
        self.assertFalse(s.at(self.root, s.READY).exists())

    def test_natural_expiry_blocks_direct_gate_after_valid_startup(self):
        self.authorization["expires_monotonic_ns"] = self.now + 15_500_000_000
        repin(self, s.FIRST_AUTHORIZATION, json.dumps(self.authorization).encode())
        reselect(self)
        self.assertEqual(self.cli(publish=True)[0], 0)
        self.assertEqual(self.cli("gate")[0], 1)

    def test_changed_authorization_blocks_publication(self):
        def change():
            value = {**self.authorization, "expires_monotonic_ns": self.now + 600_000_000_000}
            repin(self, s.FIRST_AUTHORIZATION, json.dumps(value).encode())
        self.changer = change
        self.assertEqual(self.cli(publish=True)[0], 1)
        self.assertFalse(s.at(self.root, s.READY).exists())

    def test_geometry_owner_mask_and_horizon_still_refuse(self):
        readiness_tests.ReadinessTests.test_geometry_owner_mask_and_horizon_refused(self)

    def test_sequence_and_service_identity_still_refuse(self):
        readiness_tests.ReadinessTests.test_sequence_and_identity_changes_refused(self)

    def test_stop_wins_over_first_test_publication_and_gate(self):
        startup_tests.StartupTests.test_stop_wins_over_publication_and_gate_in_flight(self)

    def test_superseded_first_test_publisher_cannot_delete_new_record(self):
        startup_tests.StartupTests.test_superseded_attempt_cannot_publish_or_delete_new_record(self)

    def test_untrusted_first_test_publication_still_refuses(self):
        startup_tests.StartupTests.test_record_symlink_hardlink_and_parent_attacks_refused(self)

    def test_staged_service_arguments_preserve_first_test_boundary(self):
        # These actual unit argv must still use the protected installed frontends.
        import configparser
        import shlex

        from test_m4_j715_audio_ready import ROOT
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / "packaging/j715-audio/speakersafetyd-j715.service")
        self.assertEqual(s.main(shlex.split(unit["Service"]["ExecCondition"])[3:], self.root), 0)
        for stage in ("ExecStartPre", "ExecStartPost"):
            command = shlex.split(unit["Service"][stage])
            if stage == "ExecStartPost":
                self.assertEqual(command[:3], ["/usr/bin/chrt", "--other", "0"])
                command = command[3:]
            self.assertEqual(r.main(command[3:], self.root), 0)
        self.assertEqual(s.main([], self.root), 1)
        self.assertEqual(r.main(shlex.split(unit["Service"]["ExecStopPost"])[3:], self.root), 0)
        self.assertFalse(s.at(self.root, s.READY).exists())

    def test_qualified_replacement_does_not_reuse_first_selection(self):
        self.assertEqual(self.cli(publish=True)[0], 0)
        self.write(s.MANIFEST, json.dumps(self.qualified_artifact))
        self.assertEqual(self.cli("gate")[0], 1)
        self.assertEqual(s.main(["--require-registration"], self.root), 1)


class FirstRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registration_tests.RegistrationTests.setUpClass()

    def setUp(self):
        self.fixture = registration_tests.RegistrationTests("test_complete_registration_population_order")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        authorize(self.fixture, registered=False)
        self.fixture.r.manifest = self.fixture.manifest

    def test_full_provider_codec_and_module_order_is_retained(self):
        fixture = self.fixture
        real_register, real_class = registration.register, registration.Registration
        helper_calls = []
        def through_main(instance, runtime, identity):
            self.assertEqual(runtime, fixture.runtime)
            self.assertEqual(identity["boot"], "fixture-boot")
            hardware_command = instance.command
            def command(argv):
                if argv[-1] == s.HELPER:
                    helper_calls.append(argv)
                    return fixture.report.decode()
                return hardware_command(argv)
            def construct(manifest, root, supplied_command):
                self.assertIsNone(supplied_command)
                return real_class(manifest, root, command, instance.sleep)
            with (patch.object(registration, "Registration", side_effect=construct),
                  patch.object(registration, "register", new=real_register)):
                self.assertEqual(registration.main(fixture.root), 0)
        with patch.object(registration, "register", side_effect=through_main):
            fixture._complete_registration_population_order(delayed=True, busy_udev=True)
        self.assertEqual(helper_calls, [["/usr/bin/python3", "-B", s.HELPER]])
        self.assertFalse(s.artifacts(fixture.root)[2]["hardware_qualified"])

    def test_stale_authorization_has_no_attempt_or_load(self):
        fixture = self.fixture
        fixture.authorization["expires_monotonic_ns"] = fixture.now
        repin(fixture, s.FIRST_AUTHORIZATION, json.dumps(fixture.authorization).encode())
        self.assertEqual(registration.main(fixture.root, fixture.command), 1)
        self.assertFalse((fixture.runtime / "attempt.json").exists())
        self.assertFalse(any(command[0] in ("insmod", "modprobe") for command in fixture.commands))

    def test_failed_load_retains_attempt_across_authorization_replacement(self):
        fixture = self.fixture
        loads = []
        def command(argv):
            if argv[0] == "insmod":
                loads.append(argv)
                raise RuntimeError("synthetic load interruption")
            return fixture.command(argv)
        fixture.r.command = command
        with self.assertRaises(RuntimeError):
            registration.register(fixture.r, fixture.runtime, {"boot": "fixture-boot"})
        self.assertTrue((fixture.runtime / "failure.json").exists())
        fixture.authorization["expires_monotonic_ns"] = fixture.now + 600_000_000_000
        repin(fixture, s.FIRST_AUTHORIZATION, json.dumps(fixture.authorization).encode())
        self.assertEqual(registration.main(fixture.root, command), 1)
        self.assertEqual(len(loads), 1)
        self.assertFalse((fixture.runtime / "registered.json").exists())

    def test_common_lock_excludes_first_registration(self):
        registration_tests.RegistrationTests.test_common_lock_excludes_registration(self.fixture)


class FirstStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package_tests.NativePackageTests.setUpClass()

    def test_inactive_assembler_issues_no_first_test_permission(self):
        fixture = package_tests.NativePackageTests("test_audio_cli_inventory_binds_every_staged_file")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = fixture.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads((fixture.output / "payload-receipt.json").read_bytes())
        for name in (s.MANIFEST, s.FIRST_AUTHORIZATION, s.FIRST_PROTOCOL):
            self.assertNotIn(name, value["inventory"])
        self.assertFalse(value["hardware_qualified"])
        self.assertFalse(value["activated"])
        self.assertFalse(value["qualification_receipt_present"])


if __name__ == "__main__":
    unittest.main()
