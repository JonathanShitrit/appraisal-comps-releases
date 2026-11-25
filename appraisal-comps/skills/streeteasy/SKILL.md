---
name: streeteasy
description: How to find and read a sold listing on StreetEasy. Text for the cloud reader that cloud_read.py starts; not for reading in Chrome.
---

# StreetEasy

Outline only. The maintainer revises it after watched runs.

## Find the property

- Search by the full address; for a condo or co-op, include the unit.
- Open the result whose street address and unit match exactly. Skip similar addresses.
- When a sale date is given, prefer the page whose price history shows a sale on that date.

## Where each field sits

- `sale_price`: the sold price near the top of the page or in the price history.
- `sale_date`: the date on the sold row of the price history.
- `contract_date`: the "In contract" row of the price history, if the page has one.
- `list_price`: the last listing row before the sale.
- `status`: the status word near the price (sold, active, in contract).
- `beds`, `baths`: the facts row near the price.
- `living_sqft`: the "ft²" or "sqft" figure in the facts row, if the page prints one.
- `year_built`: "Built in" under the building details.
- `mls_number`: only if the page prints one.

## Dates

- The close date is the sold row in the price history, not the listing date.
- A contract date is an "In contract" row.

## Ignore

- Estimates and "similar listings" or "nearby" sections.
- Ads and agent contact forms: never fill one in.
