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
STORAGES["default"] = {  # noqa: F405
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        "bucket_name": env("SUPABASE_STORAGE_BUCKET", default="sathify-media"),
        "endpoint_url": env("SUPABASE_STORAGE_ENDPOINT", default=""),
        "access_key": env("SUPABASE_STORAGE_ACCESS_KEY", default=""),
        "secret_key": env("SUPABASE_STORAGE_SECRET_KEY", default=""),
        "region_name": env("SUPABASE_STORAGE_REGION", default="ap-south-1"),
        "default_acl": "private",   # KYC documents are never publicly readable
        "querystring_auth": True,   # served via short-lived signed URLs
        "querystring_expire": 300,
        "file_overwrite": False,
        "signature_version": "s3v4",
    },
}
