#!/usr/bin/env python3
"""
Sathify - one-command developer setup.

    python scripts/setup.py

Takes a fresh clone to a running backend with demo data, and a Flutter app
ready to launch. Everything it does is also written out step by step in the
README; this script exists because four people typing the same eight commands
is four chances to mistype one, and the failure modes (a missing `.env`, a
skipped `migrate`) surface much later as confusing errors.

Idempotent and safe to re-run: an existing virtualenv is reused, an existing
`.env` is never overwritten, and `seed_demo` updates its own records in place.

Standard library only, and Python is already a prerequisite for the backend -
so this runs on every machine the project runs on, with nothing to install
first.

Options:
    --backend-only    skip the Flutter half
    --mobile-only     skip the Django half
    --no-seed         create the database but leave it empty
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
MOBILE = REPO_ROOT / "mobile"
VENV = BACKEND / ".venv"

#: Django 5.2 requires 3.10+; the deployed target (render.yaml) is 3.13.
MIN_PYTHON = (3, 10)


# --- Output -----------------------------------------------------------------
# Plain ASCII markers rather than colour codes or box-drawing: this is read in
# PowerShell, Terminal and whatever the CI log viewer is, and only one of those
# reliably renders the pretty version.

def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def info(message: str) -> None:
    print(f"    {message}", flush=True)


def fail(message: str) -> None:
    print(f"\nSETUP FAILED: {message}\n", file=sys.stderr, flush=True)
    raise SystemExit(1)


# --- Helpers ----------------------------------------------------------------

def run(command: list[str], *, cwd: Path, what: str) -> None:
    """Run a command, streaming its output, and stop the script if it fails.

    Failing loudly matters more than usual here: a half-finished setup that
    reports success is worse than no setup, because the next error the
    developer sees will be somewhere else entirely.
    """
    info(f"$ {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd)
    if result.returncode != 0:
        fail(f"{what} failed (exit {result.returncode}). See the output above.")


def venv_python() -> Path:
    """Interpreter inside backend/.venv, wherever this platform puts it."""
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def copy_env_template(directory: Path) -> None:
    """Create `.env` from `.env.example`, never clobbering an existing one.

    Both `.env` files are git-ignored and hold per-developer values, so
    overwriting one would silently discard somebody's database URL or API key.
    """
    env_file = directory / ".env"
    template = directory / ".env.example"

    if env_file.exists():
        info(f"{env_file.relative_to(REPO_ROOT)} already exists - left untouched.")
        return
    if not template.exists():
        fail(f"{template.relative_to(REPO_ROOT)} is missing from the repository.")

    shutil.copyfile(template, env_file)
    info(f"Created {env_file.relative_to(REPO_ROOT)} from .env.example.")


# --- Backend ----------------------------------------------------------------

def setup_backend(*, seed: bool) -> None:
    step("Backend - virtual environment")
    if venv_python().exists():
        info(f"Reusing the existing virtualenv at {VENV.relative_to(REPO_ROOT)}.")
    else:
        run([sys.executable, "-m", "venv", str(VENV)], cwd=BACKEND, what="venv creation")
        info(f"Created {VENV.relative_to(REPO_ROOT)}.")

    python = str(venv_python())

    step("Backend - dependencies (requirements/dev.txt)")
    # pip upgrades itself first: the wheels pinned in base.txt include some that
    # older pip resolves the slow way, building from source.
    run([python, "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        cwd=BACKEND, what="pip upgrade")
    run([python, "-m", "pip", "install", "-r", "requirements/dev.txt"],
        cwd=BACKEND, what="dependency install")
    info("The heavy CV stack (requirements/ml.txt) is NOT installed - it is")
    info("several GB and only Modules 3 and 7 need it. OCR and face checks fall")
    info("back to manual entry until you install it.")

    step("Backend - configuration")
    copy_env_template(BACKEND)
    info("Works unedited: no DATABASE_URL means local SQLite.")

    step("Backend - database")
    run([python, "manage.py", "migrate"], cwd=BACKEND, what="migrate")

    if seed:
        step("Backend - demo data")
        # Seeding rather than createsuperuser: a superuser has society=None, and
        # every society-scoped endpoint filters on it, so it logs in to an app
        # that looks broken rather than empty. seed_demo builds a real society
        # and one working login per role - and adopts any existing superuser
        # into it.
        run([python, "manage.py", "seed_demo"], cwd=BACKEND, what="seed_demo")


# --- Mobile -----------------------------------------------------------------

def setup_mobile() -> None:
    step("Mobile - configuration")
    # This has to happen BEFORE `flutter pub get` / `flutter run`: pubspec.yaml
    # declares `.env` as a bundled asset, and Flutter refuses to build at all
    # when a declared asset is missing ("No file or variants found for asset").
    copy_env_template(MOBILE)

    flutter = shutil.which("flutter")
    if flutter is None:
        step("Mobile - Flutter not found on PATH")
        info("Skipping `flutter pub get`. Install Flutter (3.27 or newer), then:")
        info("    cd mobile && flutter pub get")
        return

    step("Mobile - packages")
    run([flutter, "pub", "get"], cwd=MOBILE, what="flutter pub get")
    info("Do NOT run `flutter create` here - android/ is committed and")
    info("hand-configured; regenerating it adds files that break `flutter test`.")


# --- Entry point ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up a Sathify development environment.",
    )
    parser.add_argument("--backend-only", action="store_true",
                        help="skip the Flutter half")
    parser.add_argument("--mobile-only", action="store_true",
                        help="skip the Django half")
    parser.add_argument("--no-seed", action="store_true",
                        help="migrate but do not create demo data")
    args = parser.parse_args()

    if args.backend_only and args.mobile_only:
        fail("--backend-only and --mobile-only cannot be combined.")

    if sys.version_info < MIN_PYTHON:
        fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; this is "
            f"{sys.version.split()[0]}."
        )

    print("Sathify setup")
    print(f"  repository : {REPO_ROOT}")
    print(f"  python     : {sys.version.split()[0]} ({sys.executable})")

    if not args.mobile_only:
        setup_backend(seed=not args.no_seed)
    if not args.backend_only:
        setup_mobile()

    activate = (
        r".venv\Scripts\Activate.ps1" if os.name == "nt" else "source .venv/bin/activate"
    )
    print(f"""
================================================================
  READY
================================================================

  Start the API:
      cd backend
      {activate}
      python manage.py runserver

      API docs     http://127.0.0.1:8000/api/docs/
      Django admin http://127.0.0.1:8000/admin/

  Start the app (emulator running, API running):
      cd mobile
      flutter run

  Demo logins (password Sathify@123 for all of them):
      society_admin  9800000001
      resident       9800000002
      worker         9800000003
      guard          9800000004

  Before your first pull request, read CONTRIBUTING.md.
================================================================
""")


if __name__ == "__main__":
    main()
