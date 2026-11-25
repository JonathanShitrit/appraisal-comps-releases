---
name: zillow
description: How to find and read a sold listing on Zillow. Text for the cloud reader that cloud_read.py starts; not for reading in Chrome.
---

# Zillow

Outline only. The maintainer revises it after watched runs.

## Find the property

- Search by the full address, with the unit when there is one.
- Queens addresses are often listed without their hyphen: "72-37 136th St" is "7237 136th St". If the first search finds nothing, search again without the hyphen.
- If the start address is already a property page, read that page and do not search.
- Open the result whose street address and unit match exactly. Skip similar addresses.
- When a sale date is given, prefer the page whose price history shows a sale on that date.

## Where each field sits

- `sale_price`: the sold price in the header or the first "Sold" row of the price history.
- `sale_date`: the date on the "Sold" row of the price history.
- `contract_date`: the "Pending" or "Contract" row of the price history, if the page has one.
- `list_price`: the last "Listed for sale" row before the sale.
- `status`: the status word near the price (sold, for sale, pending).
- `beds`, `baths`: the facts row near the price ("bd", "ba").
- `living_sqft`: the "sqft" figure in the facts row or under "Interior".
- `lot_sqft`: "Lot size" under the property facts.
- `lot_front_ft`, `lot_depth_ft`: only if the page prints them; most pages do not.
- `year_built`: "Year built" under the property facts.
- `mls_number`: "MLS#" in the listing details. Letters may come before the digits (for example LP1436116): give it as shown, with no spaces.

## Dates

- The close date is the "Sold" row in the price history, not the listing date.
- A contract date is a "Pending" or "Contract" row.

## Ignore

- Estimates such as a Zestimate or a rent estimate.
- Nearby homes, similar homes and ads.
- Agent contact forms: never fill one in.
