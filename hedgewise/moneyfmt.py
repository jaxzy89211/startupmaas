"""Money on chart axes, readable for every company size (EUR 50k bills and EUR 5m bills alike).

    from hedgewise.moneyfmt import money_axis, money_unit
    ax.plot(x_eur, y_eur)                                   # plot RAW euros
    money_axis(ax, y_eur, "nl", axis="y", title="Verwachte rekening")   # -> "Verwachte rekening (€ duizend)"

The unit (€, € k / € duizend, € m / € mln) follows the size of the amounts; the number of decimals is chosen at
draw time, from ALL tick values together, as the smallest that makes every tick label different (so a EUR 132k
bill never shows "0,14 / 0,14 / 0,13"). Dutch numbers use a decimal comma and dot thousands separators.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

import numpy as np

UNITS = {
    "en": {1.0: "€", 1e3: "€ k", 1e6: "€ m"},
    "nl": {1.0: "€", 1e3: "€ duizend", 1e6: "€ mln"},
}


def _num(x: float, decimals: int, lang: str) -> str:
    s = f"{x:,.{decimals}f}"
    if s.startswith("-") and float(s.replace(",", "")) == 0:
        s = s[1:]  # no '-0'
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if lang == "nl" else s


@dataclasses.dataclass(frozen=True)
class MoneyUnit:
    """A display unit: divide by `div`, append `label`."""

    div: float
    label: str
    lang: str

    def text(self, eur: float) -> str:
        """An amount for a label on the chart, in this unit: €2.67m / €132k / €950 (en), € 2,67 mln / € 132.000."""
        return money_text(eur, self.lang, self.div)

    def fmt(self, eur: float, decimals: int | None = None) -> str:
        """One amount in this unit (decimals: 2 for millions, 0 otherwise, unless given)."""
        d = (2 if self.div == 1e6 else 0) if decimals is None else decimals
        return _num(eur / self.div, d, self.lang)


# amounts that ROUND to the next unit move to it: 999,600 is '€1.00m', not '€1,000k'
M_FROM, K_FROM = 999_500.0, 999.5


def money_text(x: float, lang: str = "en", div: float | None = None) -> str:
    """One amount in a sentence or on a chart: '€2.07m' / '€ 2,07 mln' from EUR 1m, else '€25k' / '€ 25.000' (the
    only formatter for money in text; div forces a unit: 1e6, 1e3 or 1)."""
    if div is None:
        div = 1e6 if abs(x) >= M_FROM else 1e3
    sp = "\u00a0"
    if div == 1e6:
        return f"€{_num(x / 1e6, 2, 'en')}m" if lang == "en" else f"€{sp}{_num(x / 1e6, 2, 'nl')}{sp}mln"
    if lang == "en":
        return f"€{_num(x / div, 0, 'en')}{'k' if div == 1e3 else ''}"
    return f"€{sp}{_num(round(x, -3) if div == 1e3 else x, 0, 'nl')}"


def money_unit(values: Iterable[float] | float, lang: str = "en") -> MoneyUnit:
    """The unit for amounts of this size: EUR m from EUR 1m, EUR k from EUR 1,000, else EUR."""
    v = np.abs(np.asarray(list(values) if not np.isscalar(values) else [values], dtype=float))
    vmax = float(np.nanmax(v)) if v.size and np.isfinite(v).any() else 0.0
    div = 1e6 if vmax >= M_FROM else 1e3 if vmax >= K_FROM else 1.0
    return MoneyUnit(div, UNITS[lang][div], lang)


def unique_labels(values: Iterable[float], unit: MoneyUnit, max_decimals: int = 6) -> list[str]:
    """Tick labels in `unit`, all with the same number of decimals: the fewest that show every tick value exactly
    (ticks are round numbers: 132.5 shows as '132.5', not '132'); then, as a fallback, the fewest that keep the
    labels different."""
    vals = [float(v) / unit.div for v in values]
    for d in range(max_decimals + 1):
        if all(abs(v - round(v, d)) <= 1e-9 * max(1.0, abs(v)) for v in vals):
            labels = [_num(v, d, unit.lang) for v in vals]
            if len(set(labels)) == len(labels):
                return labels
    labels: list[str] = []
    for d in range(max_decimals + 1):
        labels = [_num(v, d, unit.lang) for v in vals]
        if len(set(labels)) == len(labels):
            return labels
    return labels


def log_labels(values: Iterable[float], unit: MoneyUnit, max_decimals: int = 6) -> list[str]:
    """Log-scale ticks (1-2-5 or powers of ten) are distinct by construction: each gets the fewest decimals that
    show it exactly, so a 0.5 tick doesn't force '10.0' and '100,000.0'."""
    out = []
    for v in (float(x) / unit.div for x in values):
        d = next((d for d in range(max_decimals + 1) if abs(v - round(v, d)) <= 1e-9 * max(1.0, abs(v))), 2)
        out.append(_num(v, d, unit.lang))
    return out


def _formatter(unit: MoneyUnit):  # noqa: ANN202
    from matplotlib.ticker import Formatter

    class MoneyFormatter(Formatter):
        """Formats all ticks together (format_ticks) so the decimals can make them unique."""

        def format_ticks(self, values: list[float]) -> list[str]:
            if self.axis is not None and self.axis.get_scale() == "log":
                return log_labels(values, unit)  # 0.5 / 1 / 2 / 5 / 10: each tick with its own decimals
            return unique_labels(values, unit)

        def __call__(self, x: float, pos: int | None = None) -> str:  # single value (e.g. cursor readout)
            return unit.fmt(x, 2 if unit.div == 1e6 else 0)

    return MoneyFormatter()


def money_axis(
    ax: object,
    values: Iterable[float],
    lang: str = "en",
    axis: str = "y",
    title: str | None = None,
    fontsize: float | None = None,
) -> MoneyUnit:
    """Format a money axis whose data are RAW euros: unit from the size of `values`, unique tick labels, and the
    axis title '<title> (<unit>)' (or just the unit). Returns the unit, for labels written on the chart."""
    unit = money_unit(values, lang)
    target = ax.yaxis if axis == "y" else ax.xaxis
    target.set_major_formatter(_formatter(unit))
    from matplotlib.ticker import NullFormatter

    target.set_minor_formatter(NullFormatter())
    text = f"{title} ({unit.label})" if title else unit.label
    (ax.set_ylabel if axis == "y" else ax.set_xlabel)(text, **({"fontsize": fontsize} if fontsize else {}))
    return unit


__all__ = ["MoneyUnit", "money_text", "money_unit", "unique_labels", "log_labels", "money_axis"]
