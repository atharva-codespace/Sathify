"""
Production settings — Render free tier.

Every value here comes from real environment variables set in the Render
dashboard; there is no .env file on the server.

FREE-TIER REALITY CHECK (see docs/free-tier-constraints.md):
  * Render free web services sleep after 15 minutes of inactivity and take
    roughly 50 s to wake. That conflicts with the SRS's 2-second gate
    verification target, which is why the guard app is offline-first and
    never blocks an entry decision on a server round trip.
  * Free instances have 512 MB RAM. The PaddleOCR/TensorFlow/DeepFace stack
    does not fit. OCR and face verification run out-of-process; see the docs.
"""

import warnings

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

# Render injects its own external hostname at runtime.
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h and h != "*"]

# --- HTTPS / transport security (SRS 4.3, 5.2) ------------------------------
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# --- Media storage ----------------------------------------------------------
# Render's filesystem is EPHEMERAL: anything written to MEDIA_ROOT disappears on
# every deploy, restart, and wake-from-sleep. Worker profile photos and Aadhaar
# uploads must therefore live in Supabase Storage (S3-compatible), not on disk.
#
# The three credentials below are `sync: false` in render.yaml, so they only
# exist once somebody pastes them into the Render dashboard. Switching to the S3
# backend *before* that happens does not fail at boot — it fails on the first
# upload, deep inside boto3, as a 500 with no explanation for the worker holding
# their Aadhaar card up to the camera. So the switch is conditional: configured
# means Supabase, unconfigured means the ephemeral disk, and either way uploads
# work.
_STORAGE_ENDPOINT = env("SUPABASE_STORAGE_ENDPOINT", default="")
_STORAGE_ACCESS_KEY = env("SUPABASE_STORAGE_ACCESS_KEY", default="")
_STORAGE_SECRET_KEY = env("SUPABASE_STORAGE_SECRET_KEY", default="")

if _STORAGE_ENDPOINT and _STORAGE_ACCESS_KEY and _STORAGE_SECRET_KEY:
    STORAGES["default"] = {  # noqa: F405
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("SUPABASE_STORAGE_BUCKET", default="sathify-media"),
            "endpoint_url": _STORAGE_ENDPOINT,
            "access_key": _STORAGE_ACCESS_KEY,
            "secret_key": _STORAGE_SECRET_KEY,
            "region_name": env("SUPABASE_STORAGE_REGION", default="ap-south-1"),
            "default_acl": "private",   # KYC documents are never publicly readable
            "querystring_auth": True,   # served via short-lived signed URLs
            "querystring_expire": 300,
            "file_overwrite": False,
            "signature_version": "s3v4",
        },
    }
else:
    # Loud, because this is a real (if survivable) degradation: every uploaded
    # document is lost on the next deploy or wake-from-sleep.
    warnings.warn(
        "SUPABASE_STORAGE_ENDPOINT / _ACCESS_KEY / _SECRET_KEY are not all set, "
        "so uploaded media is being written to Render's EPHEMERAL disk and will "
        "be lost on the next restart. Set all three in the Render dashboard.",
        RuntimeWarning,
        stacklevel=1,
    )
