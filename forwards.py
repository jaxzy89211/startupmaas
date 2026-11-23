"""Thin wrapper, kept so existing commands and imports keep working. The code lives in hedgewise/forwards.py.

    python forwards.py data/forwards.csv

Imported as a module (`import forwards`), this file *is*
`hedgewise.forwards`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.forwards")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
