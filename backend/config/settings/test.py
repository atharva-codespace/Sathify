"""
Test settings.

-------------------------------------------------------------------------------
TESTS MUST NEVER REACH THE SHARED DATABASE
-------------------------------------------------------------------------------
``dev.py`` inherits ``base.DATABASES``, which reads ``DATABASE_URL`` from
``.env`` — and every developer's ``.env`` points at the shared Supabase
instance the deployed app uses. Running ``pytest`` under those settings creates
a ``test_postgres`` database *on production*, and that is not a theoretical
concern: it takes roughly fourteen minutes for a single app, the session pooler
drops connections partway through a long run (producing failures that pass in
isolation and teach the team to ignore red), and an interrupted run leaves a
locked database behind that blocks the next one.

This module pins the database locally and **ignores ``DATABASE_URL`` entirely**,
so no ``.env`` on any machine can point a test run at a real database.

Set ``TEST_DATABASE_URL`` to use local PostgreSQL instead of SQLite when you
need to exercise something SQLite cannot express — a ``JSONField`` containment
lookup, say, which is the one difference this project has actually been bitten
by (see ``apps/scheduling/services.py`` on why the weekday match runs in
Python). Everything else is faster and hermetic on SQLite.
"""

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env

DEBUG = False

# Deliberately not `env("DATABASE_URL")`. See the module docstring: the whole
# point is that the ambient value cannot leak in.
_test_database_url = env("TEST_DATABASE_URL", default="").strip()

DATABASES = {
    "default": env.db_url_config(
        _test_database_url or f"sqlite:///{BASE_DIR / 'db.test.sqlite3'}"
    )
}
# No connection reuse: the pooling that helps a long-lived server hurts a test
# run, which wants each database to be created and dropped cleanly.
DATABASES["default"]["CONN_MAX_AGE"] = 0

# --- Speed ------------------------------------------------------------------
# Password hashing dominates the runtime of any suite that creates users, and
# this project's fixtures create several per test.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Nothing may leave the machine during a test run.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Media goes to a scratch directory rather than the repo's `media/`, so a test
# that uploads a photo cannot leave files in a developer's working tree.
MEDIA_ROOT = BASE_DIR / "media_test"
STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Celery-free codebase, but the AI and payment layers read these to decide
# whether to attempt a live call. Blank means "degrade to the documented
# fallback", which is what a test should exercise.
RAZORPAY_KEY_ID = ""
RAZORPAY_KEY_SECRET = ""
