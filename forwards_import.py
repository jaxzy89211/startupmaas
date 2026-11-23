"""Thin wrapper (like the other root scripts). The code lives in hedgewise/forwards_import.py.

    python forwards_import.py export.xlsx [more.csv] [--out data/forwards.csv] [--force]

Imported as a module (`import forwards_import`), this file *is*
`hedgewise.forwards_import`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.forwards_import")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
