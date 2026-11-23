"""Thin wrapper (like the other root scripts). The code lives in hedgewise/pilot.py.

    python pilot.py data/private/<company>/ [--alias "Bedrijf A"]

Imported as a module (`import pilot`), this file *is*
`hedgewise.pilot`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.pilot")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
