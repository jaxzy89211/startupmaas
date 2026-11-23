"""Thin wrapper (like the other root scripts). The code lives in hedgewise/forwards_run.py.

    python forwards_run.py [--forwards data/forwards.csv]

Imported as a module (`import forwards_run`), this file *is*
`hedgewise.forwards_run`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.forwards_run")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
