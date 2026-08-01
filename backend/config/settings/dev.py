"""
Local development settings.

Used by manage.py by default (see manage.py). Deliberately permissive so a new
team member can clone, install, migrate and run without any external service
credentials — the database falls back to SQLite and every third-party
integration degrades to its manual/mock path when its key is absent.
"""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = ["*"]

# WhiteNoise is a production concern: it serves collected static files, and
# locally that directory does not exist until collectstatic runs, which makes it
# warn on every request. Django's own staticfiles handling covers development.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m.lower()]  # noqa: F405

# Flutter Web dev server and the Android emulator's host alias.
CORS_ALLOW_ALL_ORIGINS = True

# Show the browsable API locally — useful when hand-testing endpoints before
# the matching Flutter screen exists.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)

# Emails (OTP, receipts) print to the console instead of being sent.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
