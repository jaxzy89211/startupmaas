"""Thin wrapper (like the other root scripts). The code lives in hedgewise/extract.py.

    python extract.py bill.pdf [more.pdf] [--ai]

Imported as a module (`import extract`), this file *is*
`hedgewise.extract`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.extract")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
