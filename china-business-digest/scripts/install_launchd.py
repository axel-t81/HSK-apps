#!/usr/bin/env python3
"""Install a per-user launchd schedule for China Business Digest."""

import argparse
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile


LABEL = "com.hskapps.china-business-digest"
DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[1]
LAUNCHD_STDOUT = "launchd.stdout.log"
LAUNCHD_STDERR = "launchd.stderr.log"


class InstallError(Exception):
    """An installation request is unsafe or cannot be completed."""


def _hour(value):
    return _bounded_integer(value, "hour", 0, 23)


def _minute(value):
    return _bounded_integer(value, "minute", 0, 59)


def _bounded_integer(value, name, minimum, maximum):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if not minimum <= number <= maximum:
        raise argparse.ArgumentTypeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Write a user LaunchAgent for the digest. By default the plist is "
            "written but not loaded, and the digest itself is never run."
        )
    )
    parser.add_argument(
        "--project-dir",
        default=str(DEFAULT_PROJECT_DIR),
        help="project root containing .venv, scripts/digest.py, and the env wrapper",
    )
    parser.add_argument(
        "--env-file",
        required=True,
        help="shell env file (regular, non-symlink file; mode 0600 or stricter)",
    )
    parser.add_argument(
        "--hour", type=_hour, default=7, help="local hour, 0-23 (default: 7)"
    )
    parser.add_argument(
        "--minute", type=_minute, default=30, help="local minute, 0-59 (default: 30)"
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--print-only",
        action="store_true",
        help="print the plist without creating files or loading the agent",
    )
    action.add_argument(
        "--load",
        action="store_true",
        help="bootstrap the agent with launchctl after writing the plist",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing plist (does not imply --load)",
    )
    return parser


def _project_paths(project_arg):
    project_dir = Path(project_arg).expanduser()
    try:
        project_dir = project_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InstallError(f"Project directory does not exist: {project_dir}") from exc
    if not project_dir.is_dir():
        raise InstallError(f"Project path is not a directory: {project_dir}")

    paths = {
        "env wrapper": project_dir / "scripts" / "run_with_env.sh",
        "digest entry point": project_dir / "scripts" / "digest.py",
        "virtualenv Python": project_dir / ".venv" / "bin" / "python",
    }
    for description, path in paths.items():
        if not path.is_file():
            raise InstallError(f"Missing project {description}: {path}")

    return (
        project_dir,
        paths["env wrapper"],
        paths["digest entry point"],
        paths["virtualenv Python"],
    )


def _env_path(env_arg):
    env_file = Path(env_arg).expanduser()
    try:
        env_stat = env_file.lstat()
    except OSError as exc:
        raise InstallError(f"Environment file does not exist: {env_file}") from exc

    if stat.S_ISLNK(env_stat.st_mode):
        raise InstallError(f"Environment file must not be a symlink: {env_file}")
    if not stat.S_ISREG(env_stat.st_mode):
        raise InstallError(f"Environment file must be a regular file: {env_file}")
    if stat.S_IMODE(env_stat.st_mode) & ~0o600:
        raise InstallError(
            f"Environment file permissions must be 0600 or more restrictive: {env_file}"
        )

    try:
        return env_file.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InstallError(f"Cannot resolve environment file: {env_file}") from exc


def build_plist(project_dir, env_wrapper, digest_script, python, env_file, hour, minute):
    """Build the launchd plist dictionary from validated absolute paths."""
    if not 0 <= hour <= 23:
        raise InstallError("hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise InstallError("minute must be between 0 and 59")

    log_dir = project_dir / "build" / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/sh",
            str(env_wrapper),
            str(env_file),
            str(python),
            "-I",
            str(digest_script),
            "send",
        ],
        "WorkingDirectory": str(project_dir),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": str(log_dir / LAUNCHD_STDOUT),
        "StandardErrorPath": str(log_dir / LAUNCHD_STDERR),
    }


def launch_agent_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _replace_file(path, payload):
    """Atomically replace path without following an existing destination symlink."""
    temporary = tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix=f".{path.name}.", delete=False
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary as handle:
            handle.write(payload)
        os.replace(str(temporary_path), str(path))
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _write_plist(path, payload, log_dir, force):
    if not force and (path.exists() or path.is_symlink()):
        raise InstallError(f"Refusing to overwrite existing plist without --force: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if force:
        _replace_file(path, payload)
        return

    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise InstallError(
            f"Refusing to overwrite existing plist without --force: {path}"
        ) from exc


def _bootstrap(path, replace=False):
    if replace:
        subprocess.run(
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
            check=False,
            capture_output=True,
            text=True,
        )
    subprocess.run(
        ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        project_dir, env_wrapper, digest_script, python = _project_paths(args.project_dir)
        env_file = _env_path(args.env_file)
        plist = build_plist(
            project_dir,
            env_wrapper,
            digest_script,
            python,
            env_file,
            args.hour,
            args.minute,
        )
        payload = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)

        if args.print_only:
            sys.stdout.write(payload.decode("utf-8"))
            return 0

        destination = launch_agent_path()
        log_dir = project_dir / "build" / "logs"
        _write_plist(destination, payload, log_dir, args.force)
        if args.load:
            _bootstrap(destination, replace=args.force)
    except (InstallError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))

    if args.load:
        print(f"Wrote and loaded {destination}")
    else:
        print(f"Wrote {destination} (not loaded)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
