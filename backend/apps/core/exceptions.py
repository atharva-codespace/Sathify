"""
Uniform API error format.

The Flutter client parses one error shape for every failure, from any module:

    {
      "error": {
        "code": "validation_error",
        "message": "Enter a valid Aadhaar number.",
        "details": {"aadhaar_number": ["Checksum validation failed."]}
      }
    }

Without this, error handling in Dart would need a special case per endpoint.
"""

import logging

from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

# Maps DRF status codes onto stable, machine-readable error codes. The Flutter
# app switches on these strings, so treat them as part of the API contract.
_CODE_BY_STATUS = {
    400: "validation_error",
    401: "authentication_failed",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "throttled",
    500: "server_error",
}


def sathify_exception_handler(exc, context):
    """DRF exception handler that normalises every error response."""
    response = drf_exception_handler(exc, context)

    if response is None:
        # Not a DRF-handled exception: let Django's own 500 handling take over
        # so the traceback still reaches the logs.
        return None

    code = _CODE_BY_STATUS.get(response.status_code, "error")
    detail = response.data

    # DRF returns either {"detail": "..."} for single errors or a dict of
    # field -> [messages] for validation failures. Normalise both.
    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        message = str(detail["detail"])
        details = {}
    elif isinstance(detail, dict):
        message = "One or more fields failed validation."
        details = detail
    else:
        message = str(detail)
        details = {}

    if response.status_code >= 500:
        logger.error("Server error on %s: %s", context.get("request"), exc)

    response.data = {"error": {"code": code, "message": message, "details": details}}
    return response
