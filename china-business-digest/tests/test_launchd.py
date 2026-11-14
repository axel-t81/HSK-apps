import contextlib
import io
import os
from pathlib import Path
import plistlib
import stat
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import install_launchd  # noqa: E402


class LaunchdInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.root / "project"
        self.wrapper = self.project / "scripts" / "run_with_env.sh"
        self.digest = self.project / "scripts" / "digest.py"
        self.python = self.project / ".venv" / "bin" / "python"
        for path in (self.wrapper, self.digest, self.python):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# test fixture\n", encoding="utf-8")
        self.wrapper.chmod(0o755)
        self.python.chmod(0o755)

        self.env_file = self.root / "credentials.env"
        self.env_file.write_text("TEST_TOKEN=placeholder-value\n", encoding="utf-8")
        self.env_file.chmod(0o600)
        self.plist_path = (
            self.home
            / "Library"
            / "LaunchAgents"
            / f"{install_launchd.LABEL}.plist"
        )

        home_patch = mock.patch.object(Path, "home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        launchctl_patch = mock.patch.object(install_launchd.subprocess, "run")
        self.launchctl = launchctl_patch.start()
        self.addCleanup(launchctl_patch.stop)

    def arguments(self, env_file=None, project_dir=None):
        return [
            "--project-dir",
            str(project_dir if project_dir is not None else self.project),
            "--env-file",
            str(env_file if env_file is not None else self.env_file),
        ]

    def invoke(self, *extra, env_file=None, project_dir=None):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = install_launchd.main(
                self.arguments(env_file=env_file, project_dir=project_dir) + list(extra)
            )
        self.assertEqual(result, 0)
        return stdout.getvalue()

    def assert_failure(self, *extra, env_file=None, project_dir=None):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            install_launchd.main(
                self.arguments(env_file=env_file, project_dir=project_dir) + list(extra)
            )
        self.assertNotEqual(raised.exception.code, 0)
        self.assertIn("error:", stderr.getvalue())
        self.launchctl.assert_not_called()
        return stderr.getvalue()

    def read_plist(self):
        with self.plist_path.open("rb") as handle:
            return plistlib.load(handle)

    def test_default_plist_has_absolute_paths_arguments_and_schedule(self):
        self.invoke()

        plist = self.read_plist()
        self.assertEqual(plist["Label"], "com.hskapps.china-business-digest")
        self.assertEqual(
            plist["ProgramArguments"],
            [
                "/bin/sh",
                str(self.wrapper),
                str(self.env_file),
                str(self.python),
                "-I",
                str(self.digest),
                "send",
            ],
        )
        for argument in plist["ProgramArguments"][:4]:
            self.assertTrue(Path(argument).is_absolute())
        self.assertEqual(plist["WorkingDirectory"], str(self.project))
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 7, "Minute": 30})
        self.assertFalse(plist["RunAtLoad"])
        self.assertFalse(plist["KeepAlive"])
        log_dir = self.project / "build" / "logs"
        self.assertEqual(plist["StandardOutPath"], str(log_dir / "launchd.stdout.log"))
        self.assertEqual(plist["StandardErrorPath"], str(log_dir / "launchd.stderr.log"))
        self.assertTrue(log_dir.is_dir())
        self.launchctl.assert_not_called()

        payload = self.plist_path.read_bytes()
        self.assertNotIn(b"placeholder-value", payload)
        self.assertNotIn(b"TEST_TOKEN", payload)

    def test_print_only_uses_custom_time_and_writes_nothing(self):
        output = self.invoke("--hour", "23", "--minute", "5", "--print-only")

        plist = plistlib.loads(output.encode("utf-8"))
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 23, "Minute": 5})
        self.assertEqual(plist["ProgramArguments"][2], str(self.env_file))
        self.assertNotIn("placeholder-value", output)
        self.assertNotIn("TEST_TOKEN", output)
        self.assertFalse(self.plist_path.exists())
        self.assertFalse((self.project / "build").exists())
        self.launchctl.assert_not_called()

    def test_load_explicitly_bootstraps_agent(self):
        self.invoke("--load")

        self.launchctl.assert_called_once_with(
            [
                "/bin/launchctl",
                "bootstrap",
                f"gui/{os.getuid()}",
                str(self.plist_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_force_load_replaces_loaded_agent_before_bootstrapping(self):
        self.invoke()
        self.invoke("--hour", "8", "--force", "--load")

        self.assertEqual(self.launchctl.call_count, 2)
        bootout = self.launchctl.call_args_list[0]
        bootstrap = self.launchctl.call_args_list[1]
        self.assertEqual(
            bootout.args[0],
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{install_launchd.LABEL}"],
        )
        self.assertFalse(bootout.kwargs["check"])
        self.assertEqual(
            bootstrap.args[0],
            ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(self.plist_path)],
        )
        self.assertTrue(bootstrap.kwargs["check"])

    def test_invalid_times_and_conflicting_actions_are_rejected(self):
        invalid_options = [
            ("--hour", "-1"),
            ("--hour", "24"),
            ("--hour", "not-a-number"),
            ("--minute", "-1"),
            ("--minute", "60"),
            ("--minute", "not-a-number"),
            ("--print-only", "--load"),
        ]
        for options in invalid_options:
            with self.subTest(options=options):
                self.assert_failure(*options)
                self.assertFalse(self.plist_path.exists())

    def test_missing_symlink_and_non_regular_env_files_are_rejected(self):
        missing = self.root / "missing.env"
        with self.subTest(kind="missing"):
            self.assert_failure(env_file=missing)

        symlink = self.root / "symlink.env"
        symlink.symlink_to(self.env_file)
        with self.subTest(kind="symlink"):
            self.assert_failure(env_file=symlink)

        directory = self.root / "directory.env"
        directory.mkdir()
        with self.subTest(kind="directory"):
            self.assert_failure(env_file=directory)
        self.assertFalse(self.plist_path.exists())

    def test_more_permissive_env_modes_are_rejected(self):
        for mode in (0o640, 0o604, 0o666, 0o700):
            with self.subTest(mode=oct(mode)):
                self.env_file.chmod(mode)
                message = self.assert_failure()
                self.assertIn("permissions", message)
                self.assertFalse(self.plist_path.exists())

    def test_more_restrictive_env_mode_is_accepted(self):
        self.env_file.chmod(0o400)
        self.invoke("--print-only")
        self.assertFalse(self.plist_path.exists())

    def test_missing_project_files_are_rejected(self):
        project_files = (self.wrapper, self.digest, self.python)
        for path in project_files:
            contents = path.read_bytes()
            mode = stat.S_IMODE(path.stat().st_mode)
            path.unlink()
            with self.subTest(path=path.name):
                self.assert_failure()
                self.assertFalse(self.plist_path.exists())
            path.write_bytes(contents)
            path.chmod(mode)

        with self.subTest(path="project directory"):
            self.assert_failure(project_dir=self.root / "missing-project")

    def test_existing_plist_requires_force_and_force_replaces_it(self):
        self.invoke()
        original = self.plist_path.read_bytes()

        message = self.assert_failure("--hour", "8")
        self.assertIn("--force", message)
        self.assertEqual(self.plist_path.read_bytes(), original)
        self.assertEqual(self.read_plist()["StartCalendarInterval"]["Hour"], 7)

        self.invoke("--hour", "8", "--force")
        self.assertEqual(self.read_plist()["StartCalendarInterval"]["Hour"], 8)
        self.assertNotEqual(self.plist_path.read_bytes(), original)
        self.launchctl.assert_not_called()

    def test_force_replaces_symlink_instead_of_its_target(self):
        target = self.root / "unrelated.plist"
        target.write_bytes(b"keep me")
        self.plist_path.parent.mkdir(parents=True)
        self.plist_path.symlink_to(target)

        self.assert_failure()
        self.assertEqual(target.read_bytes(), b"keep me")

        self.invoke("--force")
        self.assertFalse(self.plist_path.is_symlink())
        self.assertEqual(target.read_bytes(), b"keep me")
        self.assertEqual(self.read_plist()["Label"], install_launchd.LABEL)


if __name__ == "__main__":
    unittest.main()
