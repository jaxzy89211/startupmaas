"""Thin wrapper (like the other root scripts). The code lives in hedgewise/market.py.

    python market.py [--offline]

Imported as a module (`import market`), this file *is*
`hedgewise.market`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.market")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
