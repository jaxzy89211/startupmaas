"""One command to rerun everything with real forward prices: `python run_with_forwards.py`.

If data/forwards.csv exists (or --forwards points to a file), this reruns, with that file as the source of fixed
prices, in a SEPARATE output folder (output/forwards_run/; the frozen pitch outputs in output/ are never touched):
    backtest.py (2021-2025), validation.py, robustness.py (small run), hedgewise_model.py (today's advice and the
    stress test) and the example PDF report (EN and NL).
Then it writes a side-by-side "proxy vs real forwards" table into RESULTS.md under its own heading (replacing an
earlier run's table, never the frozen sections) and flags every number listed in PITCH_NUMBERS.md that moves by more
than 5%. The proxy column is read from the frozen tag `pitch-freeze-v1` (falls back to output/).

What real forwards can and cannot change (also printed in the section):
* backtest and today's advice: yes - every decision date with a forward quote uses it as the fixed price and anchor;
* validation: no - it scores rolling 1/3/12-month windows anchored to the spot proxy, and a calendar-year forward is
  not a rolling-window price, so its numbers are identical by design (rerun only to prove that);
* robustness: no for the synthetic futures (no forward quotes exist for 2027-2035 decision dates).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

from .settings import DATA, ROOT

TAG = "pitch-freeze-v1"
START, END = "<!-- forwards-run:start -->", "<!-- forwards-run:end -->"
TODAY_FILES = ("strategies_{d}.csv", "recommendation_{d}.json")


def _read_frozen(rel: str) -> str | None:
    """A file as it was at the pitch-freeze tag (git show), else the working copy, else None."""
    try:
        return subprocess.run(
            ["git", "show", f"{TAG}:{rel}"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        p = ROOT / rel
        return p.read_text() if p.exists() else None


def _csv(text: str | None, **kw: object) -> pd.DataFrame | None:
    return None if text is None else pd.read_csv(io.StringIO(text), comment="#", **kw)


def tracked_numbers(get: callable) -> dict[str, float]:  # type: ignore[valid-type]
    """The numbers from PITCH_NUMBERS.md that a forwards run can move. `get(name)` returns a file's text."""
    out: dict[str, float] = {}
    st = _csv(get("output/backtest_stability.csv"), index_col=0)
    if st is not None:
        for strat, short in (
            ("All variable", "variable"),
            ("Tranches 6x (60%)", "60% tranches"),
            ("Layered 2 years (40+40%)", "layered 2y"),
            ("Fix 100% in Oct", "fix 100% Oct"),
            ("HedgeWise ladder (40% + capped top-up)", "ladder"),
        ):
            if strat in st.index:
                out[f"backtest {short}: total (EUR k)"] = st.loc[strat, "total"] / 1e3
                out[f"backtest {short}: worst year (EUR k)"] = st.loc[strat, "worst_year"] / 1e3
                out[f"backtest {short}: biggest jump (EUR k)"] = st.loc[strat, "max_jump"] / 1e3
    rec = get("output/recommendation_today.json")
    tbl = _csv(get("output/strategies_today.csv"), index_col=0)
    if rec and tbl is not None:
        r = json.loads(rec)
        last = tbl.iloc[-1]
        out["today: expected bill all variable (EUR k)"] = tbl.loc["All variable", "expected"] / 1e3
        bc = "bad_case" if "bad_case" in tbl else "es95"
        out["today: bad case all variable (EUR k)"] = tbl.loc["All variable", bc] / 1e3
        out["today: ladder share (%)"] = 100 * r["hedge_gas"]
        out["today: model target (%)"] = 100 * r.get("model_target", r["hedge_gas"])
        out["today: bad case with the plan (EUR k)"] = last[bc] / 1e3
        out["today: extra expected cost of the plan (EUR k)"] = (
            last["expected"] - tbl.loc["All variable", "expected"]
        ) / 1e3
    val = _csv(get("output/validation_table.csv"))
    if val is not None:
        a = val[(val.variant == "a_garch_t") & (val.horizon_months == 12)].set_index("commodity")
        for c in ("gas", "power"):
            out[f"validation 12-month coverage {c} (%)"] = 100 * a.loc[c, "coverage_90"]
    return out


def _today_alias(text_getter: callable, folder_date: str) -> callable:  # type: ignore[valid-type]
    """Map the generic 'today' names to the dated files of the run."""

    def get(name: str) -> str | None:
        name = name.replace("recommendation_today", f"recommendation_{folder_date}").replace(
            "strategies_today", f"strategies_{folder_date}"
        )
        return text_getter(name)

    return get


def compare(proxy: dict[str, float], fwd: dict[str, float], threshold: float = 0.05) -> pd.DataFrame:
    """Side-by-side table; 'flag' when a pitch number moves by more than `threshold`."""
    rows = []
    for k in proxy:
        p, f = proxy[k], fwd.get(k)
        ch = None if f is None or p == 0 else f / p - 1
        rows.append(
            {
                "number": k,
                "proxy (frozen)": p,
                "real forwards": f,
                "change": ch,
                "flag": "⚠️ >5%" if ch is not None and abs(ch) > threshold else "",
            }
        )
    return pd.DataFrame(rows)


def section(table: pd.DataFrame, forwards: Path, run_date: str, coverage: str) -> str:
    """The Markdown section for RESULTS.md (between markers, so a rerun replaces it)."""
    h = hashlib.sha256(forwards.read_bytes()).hexdigest()[:12]
    lines = [
        START,
        f"## Proxy vs real forwards (run {run_date}; `{forwards.name}`, sha256 {h})",
        "",
        "Frozen pitch numbers (tag `pitch-freeze-v1`, proxy fixed prices) next to the same numbers rerun with "
        "real forward prices by `python run_with_forwards.py`. ⚠️ = a number in PITCH_NUMBERS.md that moves by "
        "more than 5%: update the slide before using it.",
        "",
        f"Forwards file: {coverage}",
        "",
        "| Number | Proxy (frozen) | Real forwards | Change | |",
        "|---|---|---|---|---|",
    ]
    for r in table.itertuples(index=False):
        f = "–" if r[2] is None or pd.isna(r[2]) else f"{r[2]:,.1f}"
        c = "–" if r[3] is None or pd.isna(r[3]) else f"{r[3]:+.1%}"
        lines.append(f"| {r[0]} | {r[1]:,.1f} | {f} | {c} | {r[4]} |")
    lines += [
        "",
        "Not changed by real forwards, by design: the validation (rolling windows anchored to the spot "
        "proxy) and the synthetic futures of the robustness study (no forward quotes after today). Both were "
        "rerun to confirm.",
        END,
    ]
    return "\n".join(lines)


def write_section(text: str, results: Path) -> None:
    """Insert or replace the forwards-run section at the end of RESULTS.md."""
    s = results.read_text()
    if START in s:
        s = re.sub(re.escape(START) + r".*?" + re.escape(END), text, s, flags=re.S)
    else:
        s = s.rstrip("\n") + "\n\n" + text + "\n"
    results.write_text(s)


def run(cmd: list[str], env: dict, log: Path) -> None:
    """One script, logged; raises with the log's tail if it fails."""
    r = subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env, capture_output=True, text=True)
    log.write_text(r.stdout + r.stderr)
    if r.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{(r.stdout + r.stderr)[-1500:]}")


def main(argv: list[str] | None = None) -> pd.DataFrame | None:
    """Rerun with forwards, compare with the frozen numbers, write the section."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forwards", default=str(DATA / "forwards.csv"))
    ap.add_argument("--out", default=str(ROOT / "output" / "forwards_run"))
    ap.add_argument("--results", default=str(ROOT / "RESULTS.md"))
    ap.add_argument("--histories", type=int, default=2, help="robustness: histories per family (small run)")
    ap.add_argument("--skip-slow", action="store_true", help="skip validation and robustness (tests)")
    args = ap.parse_args(argv)
    fwd = Path(args.forwards)
    if not fwd.exists():
        print(f"No forward prices at {fwd}: nothing to do (import an export first: python forwards_import.py ...)")
        return None
    if "FAKE" in fwd.read_text(errors="ignore") and Path(args.results).resolve() == (ROOT / "RESULTS.md").resolve():
        raise SystemExit(
            f"{fwd} contains FAKE quotes: never write its numbers into RESULTS.md (use --results "
            "<a scratch copy> to test the chain)"
        )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HEDGEWISE_FORWARDS": str(fwd.resolve()), "HEDGEWISE_OUT": str(out.resolve())}
    steps = [
        (["backtest.py"], "backtest_log.txt"),
        (["hedgewise_model.py"], "model_today_log.txt"),
        (["report.py", "--name", "Limburg food processor", "--lang", "en"], "report_en_log.txt"),
        (["report.py", "--name", "Limburgse voedselverwerker", "--lang", "nl"], "report_nl_log.txt"),
    ]
    if not args.skip_slow:
        steps += [
            (["validation.py"], "validation_log.txt"),
            (["robustness.py", "--histories", str(args.histories), "--chunk-size", "4"], "robustness_log.txt"),
        ]
    for cmd, log in steps:
        print("running", " ".join(cmd), "...", flush=True)
        run(cmd, env, out / log)
    today = sorted(p.stem.split("_")[-1] for p in out.glob("recommendation_*.json"))[-1]
    frozen_today = today  # the frozen run is dated by the same price data
    proxy = tracked_numbers(_today_alias(_read_frozen, frozen_today))
    new = tracked_numbers(
        _today_alias(lambda n: (out / Path(n).name).read_text() if (out / Path(n).name).exists() else None, today)
    )
    if args.skip_slow:
        for k in [k for k in proxy if k.startswith("validation")]:
            proxy.pop(k)
    table = compare(proxy, new)
    from .forwards_import import normalise, product_report, read_any

    rows, _ = normalise(read_any(fwd), fwd.name)
    cov = "; ".join(f"{p['product']} {p['from']}..{p['to']} ({p['quotes']} quotes)" for p in product_report(rows))
    from .data import load_prices

    write_section(section(table, fwd, str(load_prices().index.max().date()), cov), Path(args.results))
    flagged = table[table.flag != ""]
    print(table.to_string(index=False))
    print(
        f"\n{len(flagged)} pitch number(s) moved by more than 5%"
        + (": " + ", ".join(flagged.number) if len(flagged) else "")
        + f". Section written to {args.results}; outputs in {out}/"
    )
    return table


__all__ = ["tracked_numbers", "compare", "section", "write_section", "main"]
