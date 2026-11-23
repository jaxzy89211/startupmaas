# Free sources of TTF and Dutch power calendar-year forward prices: what we checked

Checked 23 Nov 2026 for prompt A2. **Result: no free, legal source gives a history of calendar-year (Cal-YY)
settlement prices for TTF gas and Dutch baseload power that we may download automatically.** So nothing was added to
`download_data.py`. The realistic route stays a licensed export (Refinitiv/LSEG Workspace or Bloomberg via the UM
library or SBE finance department, or EEX DataSource / ICE Endex end-of-day files), loaded with
`python forwards_import.py <export> --out data/forwards.csv` (git-ignored: licensed data).

| Source | What it offers | Why it doesn't work for us |
|---|---|---|
| [EEX market data page, Dutch power futures](https://www.eex.com/de/marktdaten/strom/futures) | Settlement price, volume and open interest per contract, on the website | Display only, the last 45 days; no history and no download. EEX market data is licensed per use ([licensing policies](https://eex.com/en/market-data/licensing-policies), [DataSource general conditions](https://www.Eex.Com/fileadmin/EEX/Downloads/Market_Data/EEX_Group_DataSource/General_Terms_of_Contract/Datasource_GCs-v6_final.pdf)): automated collection from the website isn't a licensed use, so we don't scrape it. |
| [EEX final-settlement notices (PDF)](https://www.eex.com/fileadmin/Global/News/EEX/EEX_Customer_Information/2026/20260130_Customer_Information_-_FSP_Financial_Power_Futures_January_2026.pdf) | Final settlement prices of expiring monthly financial power futures | Only expiring (monthly) contracts at expiry: not the daily price of the next calendar year that a buyer fixes. |
| [ICE Endex end-of-day reports / Download Center](https://www.ice.com/products/27996665/Dutch-TTF-Gas-Futures) | Daily settlement, volume, open interest for ICE Endex contracts (TTF symbol TFM) | Access and reuse are governed by ICE's data terms; we could not confirm free access to a history of the Cal strip, and redistribution/automation requires a licence. |
| [Barchart TTF contract pages](https://www.barchart.com/futures/quotes/TGM27/price-history/download) | Price history per monthly TTF contract | Downloads need a paid plan (10/day on Barchart Plus); one monthly contract per file (a Cal-YY price would have to be built from 12 months); terms forbid scraping. No Dutch power. |
| [OilPriceAPI](https://docs.oilpriceapi.com/api-reference/futures/ttf-gas) | TTF spot history; TTF futures | Futures need a paid Professional plan. |
| [Databento](https://databento.com/catalog/xeee/XEEE.EOBI/futures/G3BY) | EEX / ICE futures data (CSV, Parquet) | Paid per data volume. |
| Yahoo Finance `TTF=F` (already used) | TTF front month, daily | Front month only, no calendar-year strip, no Dutch power; Yahoo's terms are for personal use. It feeds our spot model, not the fixed price. |
| ENTSO-E, Energy-Charts (already used) | Day-ahead spot prices | No forwards. |

Sources for the table: the EEX, ICE, Barchart, OilPriceAPI and Databento pages linked above (and an
[LSEG developer forum thread](https://community.developers.lseg.com/discussion/133153/commodities-forward-pricing-discovery-tool-in-python)
on pulling the TTF forward curve via the LSEG API, for the licensed route).

**What the team can do by hand (allowed):** read a handful of current Cal-27 settlement prices off the EEX public
page for an interview or a slide, with the date and source written next to them. That is a quote, not a dataset, and
must not be stored in the repository.
