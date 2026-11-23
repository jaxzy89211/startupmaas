"""Thin wrapper, kept so existing commands and imports keep working. The code lives in the hedgewise/ package.
See hedgewise/__init__.py for the public API.

    python hedgewise_model.py [--as-of 2021-06-30] [--company bills.xlsx] [--risk-aversion 0.8]

Imported as a module (`import hedgewise_model`), this file *is*
`hedgewise`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
