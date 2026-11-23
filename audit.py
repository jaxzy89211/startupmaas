"""Thin wrapper (like the other root scripts). The code lives in hedgewise/audit.py.

    python audit.py contracts.csv [--company bills.csv] [--lang nl]

Imported as a module (`import audit`), this file *is*
`hedgewise.audit`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.audit")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
