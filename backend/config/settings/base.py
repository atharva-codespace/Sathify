"""
Sathify — base Django settings shared by every environment.

Environment-specific overrides live in dev.py and prod.py. Nothing secret is
ever hardcoded here: every credential is read from the environment (see
.env.example for the full list and how to obtain each value).

Settings are grouped to mirror the module structure so four people working on
different modules can find their own configuration quickly.
"""

import json
import warnings
from datetime import timedelta
from pathlib import Path

import environ

# --- Paths ------------------------------------------------------------------
# BASE_DIR points at backend/ (the directory containing manage.py).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- Environment loading ----------------------------------------------------
env = environ.Env()
# Read backend/.env when present. Absent in CI and on Render, where real
# environment variables are injected by the platform instead.
env_file = BASE_DIR / ".env"
if env_file.exists():
    env.read_env(str(env_file))

# The fallback must be at least 32 bytes: SECRET_KEY is the HMAC key used to
# sign JWTs, and PyJWT warns below that (RFC 7518 s3.2). A short default would
# emit that warning on every local run and train the team to ignore warnings.
# Deployed environments always supply a real generated key.
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="dev-only-insecure-key-not-for-any-deployed-environment",
)
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# --- Applications -----------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",  # enables logout / token revocation
    "corsheaders",
    "django_filters",
    "drf_spectacular",
]

# One Django app per module in the Module & Sub-Module Specification, listed in
# build order. Each is independently ownable by a team member.
LOCAL_APPS = [
    "apps.core",           # cross-cutting: base models, permissions, resilience (Module 13)
    "apps.accounts",       # Module 1  — Identity & Access Management
    "apps.societies",      # Module 2  — Society & Resident Onboarding
    "apps.workers",        # Module 3  — Worker Onboarding & KYC (incl. OCR pipeline)
    "apps.hiring",         # Module 4  — Discovery & Hiring (recurring engagements)
    "apps.bookings",       # Module 5  — One-Day Service Booking
    "apps.scheduling",     # Module 6  — Scheduling & Task Management
    "apps.attendance",     # Module 7  — Attendance & Gate Verification (incl. DeepFace)
    "apps.payments",       # Module 8  — Payments & Payouts (Razorpay test mode)
    "apps.ratings",        # Module 9  — Ratings, Reviews & Trust Score
    "apps.notifications",  # Module 10 — Notifications (FCM)
    "apps.administration", # Module 11 — Admin, Reporting & Complaints
    "apps.ai_services",    # Module 12 — AI Layer (4-tier provider fallback)
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # must precede CommonMiddleware
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise is NOT here on purpose — see the static files note further down
    # and the block that installs it in prod.py.
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

# The Flutter app is the only client; Django templates are used solely by the
# built-in admin site, which Module 11 builds the approval queues on.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------------
# Defaults to local SQLite so the project runs before Supabase is provisioned.
# Set DATABASE_URL to the Supabase pooled connection string to switch over —
# no code change required.
#: Falls back to local SQLite so a fresh clone runs before anyone has a
#: Supabase project.
#:
#: Read as a plain string first, because ``default=`` only applies when the
#: variable is *absent* — an empty ``DATABASE_URL=`` (which is exactly what
#: ``.env.example`` used to ship, and what an unset Render env var looks like)
#: would otherwise parse to a dummy backend and fail with an ImproperlyConfigured
#: that says nothing about the cause.
_database_url = env("DATABASE_URL", default="").strip()

DATABASES = {
    "default": env.db_url_config(
        _database_url or f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}
# Reuse connections; Supabase's free tier has a low connection ceiling and
# Render's free instances restart often.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

# --- Authentication ---------------------------------------------------------
# Custom user model is set from the very first migration. Changing
# AUTH_USER_MODEL after migrations exist is extremely painful in Django, which
# is why apps.accounts.User is defined during scaffolding rather than later.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Django REST Framework --------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Deny by default: every endpoint must opt in to public access explicitly.
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "EXCEPTION_HANDLER": "apps.core.exceptions.sathify_exception_handler",
}

# --- JWT (Module 1.2) -------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=60)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=30)),
    # Rotation + blacklist together allow immediate revocation on logout or when
    # a device is reported lost (Module 1.5, Session & Device Management).
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    # Custom claims (role, society) are added by apps.accounts.serializers so the
    # Flutter app can route to the right dashboard without an extra round trip.
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.serializers.SathifyTokenObtainPairSerializer",
}

# --- API schema -------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    "TITLE": "Sathify API",
    "DESCRIPTION": "Smart Society Domestic Workforce Management System — JSON API "
                   "consumed by the Sathify Flutter application.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # The same choice set reached under two field names ("reason" on the
    # transition action, "end_reason" on the engagement itself) generates two
    # differently-named components, and the Dart client would then be generated
    # with two enums for one concept. Pin the name.
    "ENUM_NAME_OVERRIDES": {
        "EngagementEndReasonEnum": "apps.hiring.models.EngagementEndReason.choices",
        # Three different choice sets reach the client as a field named
        # "purpose" — consent records, the consent grant request, and OTP codes.
        # Left alone they generate arbitrary suffixed names like
        # "Purpose346Enum", which the Dart client would be generated against.
        "ConsentPurposeEnum": "apps.workers.models.ConsentPurpose.choices",
        "OtpPurposeEnum": "apps.accounts.models.OtpPurpose.choices",
        # Reached as both "decision" on an attendance event and
        # "recommendation" on a scan result.
        "GateDecisionEnum": "apps.attendance.models.Decision.choices",
        # Collides with the engagement end-reason above, both named "reason".
        "DisputeReasonEnum": "apps.payments.models.DisputeReason.choices",
        # Two unrelated choice sets both reach the client as "direction":
        # a gate entry/exit, and which way a rating runs.
        "RatingDirectionEnum": "apps.ratings.models.RatingDirection.choices",
        "GateDirectionEnum": "apps.attendance.models.Direction.choices",
        # One choice set reached as both "push_state" and "sms_state".
        "DeliveryStateEnum": "apps.notifications.models.DeliveryState.choices",
        # Module 11 introduced a second "category" and a second "status" that
        # reach the client as bare enums, so both sides of each pair are pinned.
        "NotificationCategoryEnum": "apps.notifications.models.NotificationCategory.choices",
        "ComplaintCategoryEnum": "apps.administration.models.ComplaintCategory.choices",
        "ComplaintStatusEnum": "apps.administration.models.ComplaintStatus.choices",
    },
}

# --- Internationalisation ---------------------------------------------------
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# Multilingual, icon-heavy UI is core MVP scope, not future scope — many workers
# will not read English comfortably.
LANGUAGES = [
    ("en", "English"),
    ("hi", "हिन्दी"),
    ("mr", "मराठी"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]

# --- Static & media ---------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# -----------------------------------------------------------------------------
# WHITENOISE IS CONFIGURED IN prod.py, NOT HERE
# -----------------------------------------------------------------------------
# `whitenoise` is listed in requirements/prod.txt only, and Render's build runs
# `pip install -r requirements/prod.txt`. Naming it in this file made every other
# environment reference a package it does not have installed: dev.py coped by
# stripping the middleware back out again, test.py did not, and the whole suite
# died on CI with `ModuleNotFoundError: No module named 'whitenoise'` — while
# passing on any machine that happened to have it. A base module must not name a
# dependency base.txt does not install.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- CORS -------------------------------------------------------------------
# A Flutter Android build sends no Origin header, so CORS mainly matters for
# Flutter Web during development and for the browsable schema UI.
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True

# ===========================================================================
# Third-party service configuration
#
# Every value below is read from the environment. Absent keys must degrade to a
# documented manual fallback rather than raising at import time — the SRS
# requires AI features to degrade gracefully (SRS 2.5, 5.3).
# ===========================================================================

# --- Module 12: AI layer, four-tier provider fallback -----------------------
# Tried strictly in order until one succeeds. Model IDs are configuration, not
# code, so swapping a model is a one-line .env change.
AI_SETTINGS = {
    "ENABLED": env.bool("AI_ENABLED", default=True),
    "TIMEOUT_SECONDS": env.int("AI_TIMEOUT_SECONDS", default=30),
    "TIERS": [
        {
            # TIER 1 — Google Gemini. Primary; best quality-to-cost ratio and the
            # tier prompts are designed against first.
            "name": "gemini",
            "api_key": env("GEMINI_API_KEY", default=""),
            "model": env("GEMINI_MODEL", default="gemini-2.0-flash"),
            "endpoint": env(
                "GEMINI_ENDPOINT",
                default="https://generativelanguage.googleapis.com/v1beta/models",
            ),
        },
        {
            # TIER 2 — Groq (the LPU inference company, NOT xAI's Grok).
            # Model is config-driven: may point at Llama or Qwen3-32B.
            "name": "groq",
            "api_key": env("GROQ_API_KEY", default=""),
            "model": env("GROQ_MODEL", default="qwen/qwen3-32b"),
            "endpoint": env(
                "GROQ_ENDPOINT",
                default="https://api.groq.com/openai/v1/chat/completions",
            ),
        },
        {
            # TIER 3 — OpenRouter free catalog. Model ID must end in ":free".
            #
            # WARNING — NOT SET AND FORGET: OpenRouter's free model catalog
            # rotates fairly often, and a model that is free today may be
            # withdrawn or made paid later. Expect to update OPENROUTER_MODEL
            # periodically; Tiers 1 and 2 do not need this maintenance.
            #
            # Free-tier ceiling: 20 requests/minute and 50 requests/DAY per
            # account. (The 1,000/day ceiling requires a one-time $10 credit
            # purchase, which is out of scope for a zero-budget project.) The
            # AI layer therefore enforces a local daily cap on this tier.
            "name": "openrouter",
            "api_key": env("OPENROUTER_API_KEY", default=""),
            "model": env("OPENROUTER_MODEL", default="deepseek/deepseek-r1-distill-llama-70b:free"),
            "endpoint": env(
                "OPENROUTER_ENDPOINT",
                default="https://openrouter.ai/api/v1/chat/completions",
            ),
            "daily_request_cap": env.int("OPENROUTER_DAILY_CAP", default=50),
            "per_minute_cap": env.int("OPENROUTER_MINUTE_CAP", default=20),
        },
        {
            # TIER 4 — Hugging Face Inference API. Last resort.
            "name": "huggingface",
            "api_key": env("HUGGINGFACE_API_KEY", default=""),
            "model": env("HUGGINGFACE_MODEL", default="meta-llama/Llama-3.1-8B-Instruct"),
            "endpoint": env(
                "HUGGINGFACE_ENDPOINT",
                default="https://router.huggingface.co/v1/chat/completions",
            ),
        },
    ],
}
# NOTE: xAI's Grok is deliberately absent. As of mid-2026 xAI offers no durable
# no-strings free API tier — its "free credits" require opting into a
# data-sharing programme, which fails this project's zero-budget rule (you pay
# with usage data). If a confirmed genuinely-free key is obtained later, it can
# be appended as a fifth tier here with no other code change.

# --- Module 3: OCR pipeline -------------------------------------------------
OCR_SETTINGS = {
    "PRIMARY_ENGINE": env("OCR_PRIMARY_ENGINE", default="paddleocr"),
    "FALLBACK_ENGINE": env("OCR_FALLBACK_ENGINE", default="easyocr"),
    "LANGUAGES": env.list("OCR_LANGUAGES", default=["en"]),
    # Below this confidence a critical field (especially the Aadhaar number) is
    # NEVER auto-filled — it is flagged in the Flutter form for the worker to
    # confirm or correct manually.
    "CONFIDENCE_THRESHOLD": env.float("OCR_CONFIDENCE_THRESHOLD", default=0.85),
    "MAX_UPLOAD_BYTES": env.int("OCR_MAX_UPLOAD_BYTES", default=10 * 1024 * 1024),
}

# --- Module 4: Discovery & hiring --------------------------------------------
# How long a worker has to answer a hire request before it lapses. Expiry is
# evaluated lazily on read (no scheduled worker on the free tier), so changing
# this affects requests already in flight as well as new ones.
HIRE_REQUEST_RESPONSE_HOURS = env.int("HIRE_REQUEST_RESPONSE_HOURS", default=48)

# --- Module 7: Face verification --------------------------------------------
FACE_SETTINGS = {
    "ENABLED": env.bool("FACE_VERIFICATION_ENABLED", default=True),
    "MODEL": env("FACE_MODEL", default="Facenet"),
    "DETECTOR_BACKEND": env("FACE_DETECTOR_BACKEND", default="opencv"),
    "DISTANCE_METRIC": env("FACE_DISTANCE_METRIC", default="cosine"),
    # A failed match NEVER hard-blocks entry. It surfaces "Not Verified" in the
    # guard app and requires an explicit, separately-logged manual override.
    # A false rejection has a real livelihood cost for the worker.
    "ALLOW_MANUAL_OVERRIDE": True,
}

# --- Module 8: Payments (Razorpay TEST MODE ONLY) ---------------------------
RAZORPAY_SETTINGS = {
    "KEY_ID": env("RAZORPAY_KEY_ID", default=""),
    "KEY_SECRET": env("RAZORPAY_KEY_SECRET", default=""),
    "WEBHOOK_SECRET": env("RAZORPAY_WEBHOOK_SECRET", default=""),
    # Guard rail: the app refuses to start a live transaction while this is True.
    "TEST_MODE": env.bool("RAZORPAY_TEST_MODE", default=True),
    "CURRENCY": "INR",
}

# --- Module 8.9: UPI QR -----------------------------------------------------
#
# There is deliberately no separate configuration here any more.
#
# The first version of this feature collected into a plain VPA set by
# `UPI_VPA`, which meant money arrived in a bank account with no callback and
# nothing could mark the payment paid without a human reading a statement. The
# QR is now opened through Razorpay (`apps/payments/upi.py`), so it settles
# through the same signed webhook a card payment does — and the only thing that
# needs configuring is RAZORPAY_SETTINGS above.
#
# If you still have UPI_VPA / UPI_PAYEE_NAME / UPI_MCC in a .env, they are dead
# and can be deleted.

# --- Module 8.7: platform fees ----------------------------------------------
# Off. The column, the calculation and the receipt line all ship before the
# price does, so that turning pricing on is a one-line change rather than a
# schema change made under deadline pressure on launch day.
#
# The intended rate lives in `apps/payments/fees.py`; this decides whether it is
# charged. Flipping it to True starts charging residents real money on one-day
# bookings — never on a recurring salary, which is a wage transfer.
PLATFORM_FEES_ENABLED = env.bool("PLATFORM_FEES_ENABLED", default=False)

# --- Module 10: Notifications (FCM) -----------------------------------------
FCM_SETTINGS = {
    "ENABLED": env.bool("FCM_ENABLED", default=False),
    "CREDENTIALS_PATH": env("FCM_CREDENTIALS_PATH", default=""),
    "PROJECT_ID": env("FCM_PROJECT_ID", default=""),
}

# --- SMS: Module 1.4 OTP delivery and Module 10.2 notification fallback -------
# Provider-agnostic on purpose. Indian SMS gateways (MSG91, Textlocal, Fast2SMS)
# all expose a simple authenticated HTTP POST, so the provider is configuration
# rather than code, and no vendor SDK is pulled in for one request.
#
# Filling these in is what makes OTPs arrive on real phones: apps.accounts
# selects its backend from this config, so there is no second switch to set.
# With it off, codes print to the runserver console in DEBUG and nothing is
# delivered at all otherwise.
#
# Disabled by default: SMS costs real money per message and there is no account
# on this project. With it off, a failed push is recorded as undelivered and the
# in-app notification centre still holds the message — which is the point of
# Module 10.3 existing alongside the push channel.
#
# INDIA: carriers require the sender to be DLT-registered (TRAI). A gateway
# account alone is not enough — the header and the message template must both
# be registered, or messages are accepted by the API and dropped by the carrier.
# ``SMS_BACKEND`` forces a specific backend; leave it empty to choose from config.
SMS_BACKEND = env("SMS_BACKEND", default="")


def _sms_extra_params() -> dict:
    """Parse SMS_EXTRA_PARAMS, tolerating a blank or malformed value.

    ``env.json`` raises on an empty string, and an empty string is exactly what
    a half-filled ``.env`` contains — ``SMS_EXTRA_PARAMS=`` with nothing after
    it. Letting that propagate takes the whole server down at import time, on a
    setting that only matters to one optional gateway field. Degrading to "no
    extra params" keeps the server up and puts the problem where it can be read.
    """
    raw = env("SMS_EXTRA_PARAMS", default="").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        warnings.warn(
            f"SMS_EXTRA_PARAMS is not valid JSON and was ignored: {raw!r}",
            stacklevel=2,
        )
        return {}

    if not isinstance(parsed, dict):
        warnings.warn(
            f"SMS_EXTRA_PARAMS must be a JSON object, got {type(parsed).__name__}; ignored.",
            stacklevel=2,
        )
        return {}
    return parsed
SMS_SETTINGS = {
    "ENABLED": env.bool("SMS_ENABLED", default=False),
    "ENDPOINT": env("SMS_ENDPOINT", default=""),
    "API_KEY": env("SMS_API_KEY", default=""),
    "SENDER_ID": env("SMS_SENDER_ID", default="SATHFY"),
    # Field names differ per gateway; naming them here avoids a code change to
    # switch provider.
    "TO_FIELD": env("SMS_TO_FIELD", default="to"),
    "MESSAGE_FIELD": env("SMS_MESSAGE_FIELD", default="message"),
    # How the key is presented. The Indian gateways disagree about this and the
    # disagreement is the whole reason a request gets a 401 rather than a
    # delivery: MSG91 wants `authkey: <key>`, Fast2SMS wants
    # `authorization: <key>` with no scheme word, Textlocal wants it as a form
    # field. Defaults match the RFC-style `Authorization: Bearer <key>`.
    "AUTH_HEADER": env("SMS_AUTH_HEADER", default="Authorization"),
    #: Empty means send the bare key with no scheme prefix.
    "AUTH_SCHEME": env("SMS_AUTH_SCHEME", default="Bearer"),
    # Static form fields the gateway requires beyond to/message — Fast2SMS
    # needs `route`, some need `flash` or a DLT template id. JSON so a new
    # provider requirement never needs a code change.
    "EXTRA_PARAMS": _sms_extra_params(),
}

# --- Logging ----------------------------------------------------------------
# The AI layer logs which tier served each request at DEBUG level so we can see
# in testing how often calls fall past Tier 1.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} [{name}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "apps": {
            "handlers": ["console"],
            "level": env("SATHIFY_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
        "apps.ai_services": {
            "handlers": ["console"],
            "level": env("AI_LOG_LEVEL", default="DEBUG"),
            "propagate": False,
        },
    },
}
