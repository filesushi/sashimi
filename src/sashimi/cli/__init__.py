# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``sashimi`` command line. Needs the ``cli`` extra: ``pip install 'sashimi-convert[cli]'``."""

import sys


def main() -> None:
    try:
        import rich  # noqa: F401
        import tomlkit  # noqa: F401
        import typer  # noqa: F401
    except ImportError as e:
        sys.stderr.write(
            f"The sashimi command line needs extra packages ({e.name} is missing).\n"
            "Install them with:  pip install 'sashimi-convert[cli]'\n"
        )
        raise SystemExit(1) from None

    from sashimi.cli.main import app

    app()
