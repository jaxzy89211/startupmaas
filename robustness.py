"""Thin wrapper, kept so the command is short. The code lives in hedgewise/robustness.py.

    python robustness.py            # robustness of the strategies on 2,003 synthetic histories (~45 min, all CPU cores)

Imported as a module (`import robustness`), this file *is*
`hedgewise.robustness`: it replaces itself in sys.modules,
so attributes and monkeypatching behave exactly as on the package module.
"""

import importlib
import sys

_impl = importlib.import_module("hedgewise.robustness")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
