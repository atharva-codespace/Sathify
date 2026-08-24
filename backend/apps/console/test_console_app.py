"""
The console's own shell, and a guard against it drifting from the API.

The second class here is the important one. The console is plain JavaScript with
no build step and no type checking, so nothing stops a screen from calling an
endpoint that does not exist — and the failure would be a 404 in a browser
console that nobody is watching, on a screen an operator uses once a month.

So every API path the script mentions is extracted and resolved against the real
URLconf. That is the check a compiler would have done.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from django.urls import Resolver404, resolve, reverse

pytestmark = pytest.mark.django_db

CONSOLE_JS = (
    Path(__file__).resolve().parent / "static" / "console" / "console.js"
)
CONSOLE_CSS = (
    Path(__file__).resolve().parent / "static" / "console" / "console.css"
)

#: Substitutions tried for a `${...}` interpolation in a JS path, in order.
#: One of them has to make the path resolve, which is what proves the route the
#: script is aiming at genuinely exists.
PLACEHOLDERS = [str(uuid.uuid4()), "1", "csv", "SA-9931"]


def _js_api_paths() -> set[str]:
    """Every `/api/v1/...` path the console script requests."""
    source = CONSOLE_JS.read_text(encoding="utf-8")
    # request('/console/…') and request(`/console/…`)
    return set(re.findall(r"request\(\s*[`'\"]([^`'\"]+)[`'\"]", source))


def _resolves(path: str) -> bool:
    slots = re.findall(r"\$\{[^}]+\}", path)
    if not slots:
        try:
            resolve(f"/api/v1{path}")
            return True
        except Resolver404:
            return False

    # Try each placeholder in every slot. Crude, and enough: a path that
    # resolves for no substitution at all is a path that does not exist.
    for value in PLACEHOLDERS:
        candidate = path
        for slot in slots:
            candidate = candidate.replace(slot, value, 1)
        try:
            resolve(f"/api/v1{candidate}")
            return True
        except Resolver404:
            continue
    return False


class TestTheConsoleShell:
    def test_it_serves_without_authentication(self, client):
        """The shell carries no data — a visitor sees a login form, nothing else."""
        response = client.get(reverse("v1:console:app"))
        assert response.status_code == 200
        assert b"SATHIFY OPS" in response.content

    def test_it_ships_no_data_in_the_markup(self, client, society, resident_user):
        """Everything comes from the JWT-gated API, never from the template."""
        body = client.get(reverse("v1:console:app")).content.decode()
        assert society.name not in body
        assert resident_user.phone_number not in body

    def test_it_tells_a_visitor_without_javascript_what_is_wrong(self, client):
        body = client.get(reverse("v1:console:app")).content.decode()
        assert "<noscript>" in body

    def test_the_static_assets_exist(self):
        assert CONSOLE_JS.exists()
        assert CONSOLE_CSS.exists()


class TestTheConsoleCallsOnlyRealEndpoints:
    """A compiler would catch this. There is no compiler, so a test does."""

    def test_it_calls_at_least_the_screens_the_prd_asks_for(self):
        paths = _js_api_paths()
        for expected in [
            "/console/overview/",
            "/console/transactions/",
            "/console/societies/",
            "/console/users/",
            "/console/reports/",
        ]:
            assert any(p.startswith(expected) for p in paths), expected

    def test_every_path_it_requests_resolves(self):
        unresolved = sorted(p for p in _js_api_paths() if not _resolves(p))
        assert not unresolved, (
            "The console requests endpoints that do not exist: " + ", ".join(unresolved)
        )

    def test_it_does_not_reach_past_the_console_api(self):
        """Auth aside, the console must not borrow the apps' own endpoints.

        Those are society-scoped and would silently return nothing for an
        operator with no society — the confusing failure, not the loud one.
        """
        allowed_prefixes = ("/console/", "/auth/")
        strays = sorted(
            p for p in _js_api_paths() if not p.startswith(allowed_prefixes)
        )
        assert not strays, f"Unexpected API surface: {strays}"


class TestTheDesignSystemIsTheAppsOwn:
    """The console and the apps are one product; the palette says so."""

    def test_the_brand_colours_match_the_flutter_tokens(self):
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        tokens = (
            Path(__file__).resolve().parents[3]
            / "mobile" / "lib" / "core" / "theme" / "app_tokens.dart"
        )
        if not tokens.exists():  # pragma: no cover — mobile/ absent in some checkouts
            pytest.skip("mobile/ is not present in this checkout")

        dart = tokens.read_text(encoding="utf-8")
        for name, hex_value in [
            ("primary", "1B6B50"),
            ("danger", "C0392B"),
            ("warning", "B5730F"),
            ("success", "2E7D32"),
        ]:
            assert f"0xFF{hex_value}" in dart, f"{name} moved in the Flutter tokens"
            assert f"#{hex_value}" in css, (
                f"--{name} in console.css no longer matches the app's {name}"
            )
