"""Shared type aliases."""

from __future__ import annotations

from pathlib import Path
from typing import IO, TypeAlias

import pandas as pd

DateLike: TypeAlias = str | pd.Timestamp  # anything pd.Timestamp() accepts in practice: "2026-11-23" or a Timestamp
PathOrBuffer: TypeAlias = str | Path | IO[bytes]  # a file path or an in-memory binary buffer
