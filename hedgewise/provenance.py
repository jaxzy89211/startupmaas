"""Provenance: which config and which data produced an output file.

Every output (CSV, JSON, PNG, PDF) records:
  * config_hash  - SHA-256 (12 hex) of config.yaml's bytes
  * data_hash    - SHA-256 (12 hex) over the price files used (name + bytes, sorted), and one hash per file
  * data_from / data_to - the date range of the loaded price history
  * hedgewise_version

No timestamps on purpose: the same code + config + data gives byte-identical outputs, which makes outputs easy to
verify ("re-run and diff"). load_prices() / demo_prices() register the data; writers read it with current().
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from ._types import PathOrBuffer
from .config import config_hash, config_path

if TYPE_CHECKING:
    from matplotlib.figure import Figure

HASH_CHARS = 12
VERSION = "0.5.0"
PRICE_FILES = ("gas_ttf.csv", "power_nl_daily.csv", "co2_eua.csv", "forwards.csv", "forwards.xlsx")

_current: dict[str, Any] = {}


def file_hash(path: Path) -> str:
    """SHA-256 (first 12 hex characters) of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:HASH_CHARS]


def register_data(df: pd.DataFrame, data_dir: Path | None, extra_files: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Record the loaded price history (date range + file hashes) as the current provenance; returns it.

    data_dir=None means synthetic demo data (no files)."""
    files: dict[str, str] = {}
    if data_dir is not None:
        for name in PRICE_FILES:
            p = data_dir / name
            if p.exists():
                files[name] = file_hash(p)
    for p in extra_files:
        if p.exists():
            files[p.name] = file_hash(p)
    combined = hashlib.sha256()
    for name in sorted(files):
        combined.update(name.encode())
        combined.update(files[name].encode())
    _current.clear()
    _current.update(
        {
            "hedgewise_version": VERSION,
            "config_file": config_path().name,
            "config_hash": config_hash(),
            "data_hash": combined.hexdigest()[:HASH_CHARS] if files else "demo-data",
            "data_files": files,
            "data_from": str(pd.Timestamp(df.index.min()).date()) if len(df) else None,
            "data_to": str(pd.Timestamp(df.index.max()).date()) if len(df) else None,
        }
    )
    return dict(_current)


def current() -> dict[str, Any]:
    """The provenance of the most recently loaded data (config hash only if no data was loaded yet)."""
    if not _current:
        return {"hedgewise_version": VERSION, "config_file": config_path().name, "config_hash": config_hash()}
    return dict(_current)


def one_line(p: dict[str, Any] | None = None) -> str:
    """Compact text form, e.g. 'config 1a2b3c4d5e6f · data 9f8e7d6c5b4a (2019-01-01..2026-11-23) · v0.5.0'."""
    p = p or current()
    rng = f" ({p['data_from']}..{p['data_to']})" if p.get("data_from") else ""
    data = f" · data {p['data_hash']}{rng}" if "data_hash" in p else ""
    return f"config {p['config_hash']}{data} · v{p['hedgewise_version']}"


def write_csv(df: pd.DataFrame, path: Path, **to_csv_kwargs: Any) -> None:
    """DataFrame.to_csv with '# ' provenance lines on top (read back with pd.read_csv(..., comment='#'))."""
    p = current()
    header = "".join(f"# {k}: {json.dumps(v)}\n" for k, v in p.items())
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(header)
        df.to_csv(fh, **to_csv_kwargs)


def write_json(obj: dict[str, Any], path: Path, **dumps_kwargs: Any) -> None:
    """json.dump with an added top-level '_provenance' key."""
    Path(path).write_text(json.dumps({**obj, "_provenance": current()}, **dumps_kwargs))


def save_figure(fig: Figure, path: PathOrBuffer, **savefig_kwargs: Any) -> None:
    """fig.savefig with the provenance in the PNG's text metadata ('Description' chunk)."""
    meta = {"Software": f"HedgeWise {VERSION}", "Description": "HedgeWise provenance: " + json.dumps(current())}
    fmt = savefig_kwargs.get("format") or (Path(str(path)).suffix.lstrip(".").lower() or "png")
    if fmt == "png":
        savefig_kwargs.setdefault("metadata", meta)
    fig.savefig(path, **savefig_kwargs)
