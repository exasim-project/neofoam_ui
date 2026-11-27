# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NeoFOAM authors

"""``python -m neofoam_ui.wizard`` — build the app and start the trame server."""

from neofoam.core.entry_points import load_plugins
from neofoam_ui.wizard import build_app

if __name__ == "__main__":
    load_plugins()
    build_app().start()
