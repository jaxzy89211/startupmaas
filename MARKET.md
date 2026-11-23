# HedgeWise market size (from CBS open data)

`python market.py` (or `python market.py --offline` to use the snapshot in `data/cbs/`). Numbers: `output/market_summary.csv`,
`output/market_segments.csv`; chart: `output/market.png`. All parameters are in `config.yaml` (`market`).

## Who we count

Companies with **10–250 working persons** in eight energy-intensive sectors: food and drink (SBI 10–12), paper (17),
chemicals (20), rubber and plastics (22), glass/ceramics/cement/concrete (23), basic metals (24), metal products (25),
and greenhouse horticulture (vegetables, flowers, plants and fruit grown under glass). A size class only counts if its
companies' **estimated gas + power use is at least 1 GWh a year** (assumption: below roughly €100k of energy a year,
hedging advice isn't worth a fee). Pharma, textiles, wood, offices, hospitals, retail and so on are left out, so the
scope is deliberately narrow.

## Sources (CBS StatLine, OData, no account)

| Table | What we take from it | Period |
|---|---|---|
| `81589NED` Bedrijven; bedrijfstak | companies per sector and size class (100–250 = "0 tot 250" minus the smaller classes) | Q3 2026 |
| `81578NED` Vestigingen van bedrijven; bedrijfstak, regio | Limburg's share of each sector's establishments (`PV31` / `NL01`) | 2026 |
| `83583NED` Banen van werknemers; bedrijfsgrootte, SBI | employee jobs per sector (energy per person) | Dec 2024 |
| `83989NED` Energiebalans; aanbod en verbruik, sector | gas (final use + gas burnt in CHP) and electricity (final use) per sector, PJ × 277.8 = GWh | 2024 (final) |

## Method and assumptions

1. Energy per person in a sector = the sector's gas + power use ÷ its employee jobs. For greenhouses, which have no
   jobs breakdown: ÷ (companies × typical persons per size class, over all classes).
2. A company's use = typical persons in its size class (14 / 32 / 70 / 155; **assumption**) × energy per person.
   Checked against CBS jobs per company: the real numbers are mostly *higher* than these midpoints (by up to 44%,
   food 10–50 persons; one class 3% lower), so this step leans conservative.
3. **Biggest uncertainty:** step 2 assumes energy use is proportional to size *within* a sector. Large plants are
   usually more energy-intensive per person, so this is an **upper bound** on a mid-sized company's use. We also show
   a case where mid-sized companies use half the sector's energy per person.
4. Limburg = Dutch count × Limburg's share of the sector's establishments (5.8% food, 6–9% elsewhere).
5. Energy spend = GWh × the 2025 average TTF gas (€36/MWh) and NL day-ahead power (€87/MWh) prices. Commodity cost only.
6. Revenue = subscribers × (€3,000 a year + €150 per GWh of gas + power they use). Adoption 1%, 5% and 10% are
   **assumptions**, not research.

## The numbers

| | Netherlands | Limburg |
|---|---|---|
| Companies with 10–250 persons in these sectors | 4,435 | 297 |
| … of which use ≥ 1 GWh a year (our target) | **3,620** (3,015) | **245** (209) |
| Their gas + power use | 49.4 TWh (24.4) | 3.7 TWh (1.8) |
| Their yearly energy bill (commodity, 2025 prices) | €2.43bn (€1.20bn) | €181m (€90m) |
| Revenue if every target company subscribed | €18.3m (€12.7m) | €1.29m (€0.90m) |
| Revenue at 1% / 5% / 10% adoption | €0.18m / €0.91m / €1.83m | €13k / €64k / €129k |
| … with half the energy per person | €0.13m / €0.64m / €1.27m | €9k / €45k / €90k |

(Numbers in brackets: mid-sized companies use half the sector's energy per person.)

By sector (Netherlands / Limburg target companies): food and drink 1,175 / 68, metal products 915 / 55 (the 10–20
class is below 1 GWh and excluded), greenhouses 500 / 35, rubber and plastics 295 / 25 (10–20 excluded), chemicals
285 / 27, glass, ceramics and cement 235 / 19, paper 135 / 10, basic metals 80 / 6.

## What this means

* **A customer is worth about €5,000 a year** (€3,000 + €150 × ~14 GWh). In the Netherlands, 10% adoption (≈360
  customers) gives €1.3–1.8m a year. **Limburg alone is too small for a business** (≈25 customers at 10%, about
  €0.1m a year). It is the pilot region, not the market.
* The per-GWh fee is about 40% of revenue at these averages. Greenhouses and chemicals use 63% of the target
  group's energy, so they carry most of it.
* What would move the numbers most: the real energy use of mid-sized companies (ask in every interview), the
  adoption rate, and adding sectors (pharma, cold storage and logistics, hospitals, water companies).
* CBS rounds company counts to 5 and its 2026 counts are provisional; 2025 energy data is provisional, so we use 2024.
