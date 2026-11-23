"""Thin wrapper (like the other root scripts). The code lives in hedgewise/monitor.py.

    python monitor.py [--no-download] [--as-of YYYY-MM-DD]

Imported as a module (`import monitor`), this file *is*
`hedgewise.monitor`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.monitor")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
