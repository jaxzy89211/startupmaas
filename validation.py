"""Thin wrapper, kept so the command is short. The code lives in hedgewise/validation.py.

    python validation.py            # out-of-sample validation of the price model (about 5-10 minutes)

Imported as a module (`import validation`), this file *is*
`hedgewise.validation`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.validation")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
