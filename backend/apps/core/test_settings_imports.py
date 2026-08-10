"""Whatever the active settings name, the environment must actually have.

-------------------------------------------------------------------------------
WHY THIS EXISTS
-------------------------------------------------------------------------------
``base.py`` once listed ``whitenoise.middleware.WhiteNoiseMiddleware`` while
``whitenoise`` was only in ``requirements/prod.txt``. Every environment that
installs ``dev.txt`` therefore named a package it did not have. ``dev.py`` coped
by filtering the middleware back out; ``test.py`` did not, and CI reported it as
**620 failed, 670 passed** — one ``ModuleNotFoundError`` per test that happened
to issue a request, with the real cause repeated 620 times and stated in none of
them. It passed on every developer machine that happened to have the package.

These two tests turn that into one failure that names the missing module. They
are deliberately about *importability*, not about a specific package: the rule
is that a settings module may not name a dependency its own requirements file
does not install, and any future repeat is the same mistake wearing a different
name.
"""

from __future__ import annotations

import importlib

import pytest
from django.conf import settings


def _module_of(dotted_path: str) -> str:
    """``a.b.ClassName`` -> ``a.b``."""
    return dotted_path.rsplit(".", 1)[0]


@pytest.mark.parametrize("middleware", settings.MIDDLEWARE)
def test_every_middleware_is_importable(middleware):
    try:
        importlib.import_module(_module_of(middleware))
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"MIDDLEWARE names {middleware!r}, but {exc.name!r} is not installed "
            f"in this environment. Either add it to the requirements file this "
            f"environment installs, or move the setting to the settings module "
            f"for the environment that does install it (see base.py)."
        )


@pytest.mark.parametrize("alias", sorted(settings.STORAGES))
def test_every_storage_backend_is_importable(alias):
    backend = settings.STORAGES[alias]["BACKEND"]
    try:
        importlib.import_module(_module_of(backend))
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"STORAGES[{alias!r}] names {backend!r}, but {exc.name!r} is not "
            f"installed in this environment. Same rule as the middleware test "
            f"above."
        )
