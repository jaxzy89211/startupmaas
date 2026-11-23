"""Thin wrapper, kept so existing commands and imports keep working. The code lives in hedgewise/company_input.py.

    python company_input.py bills.xlsx

Imported as a module (`import company_input`), this file *is*
`hedgewise.company_input`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.company_input")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
