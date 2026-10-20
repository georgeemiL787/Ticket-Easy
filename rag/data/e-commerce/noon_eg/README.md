# noon Egypt shop data (tenant noon_eg, e-commerce)

A much bigger version of Team A's noon Egypt shop data (tenant `noon_eg`), in exactly the files and fields
Team A's database loader already reads. Team A's database schema is unchanged.

Built 2026-10-08. The shop's fixed "today" stays 2026-10-08, like the hand-built data.

## What's in it

| | hand-built (kept as is) | added | total |
|---|---|---|---|
| Customers | 11 | 5,000 | 5,011 |
| Orders | 56 | 26,110 | 26,166 |
| Order items | 57 | 40,306 | 40,363 |
| Returns | 10 | 1,325 | 1,335 |
| Test scenarios | 67 | 900 | 967 |
| Past support tickets | 18 | 1,500 | 1,518 |

- **Customers**: fictional Egyptian names in English and Arabic, 27 cities, `@example.com` emails, fictional
  `0100 …` phones, preferred style en / ar / mixed / arabizi, about 90% verified.
- **Orders**: one year of orders (Oct 2025 to Oct 2026) with busier White Friday, Ramadan and back-to-school
  periods. Noon section and noon Minutes (fast delivery, only in Cairo, Giza, New Cairo, 6th of October and
  Alexandria). 13 product categories, ~60 product types. Statuses include delivered, returned, return in
  progress, shipped (some late), processing and cancelled. Payments: card, cash on delivery, Tabby, card EMI.
- **Returns** follow the policy timeline: pickup, up to a week to reach noon, up to 2 business days of
  checks, then the refund to card (7 to 14 business days), noon wallet (cash), bank account, or Tabby
  (14 to 21 days). Some fail the quality check and are sent back, held at the hub, or disposed of. A few
  card refunds are "Approved" but never arrived, so the customer must contact their bank.
- **Test scenarios** (900 new): 600 "can I return this?" cases covering every decision the policy can give
  (allowed, outside the window, no return tag, excluded category, bad condition, warranty, conflict,
  unverified customer); 240 "where is my refund?" cases with the policy sections and points a good answer
  must cover; 60 cancellation requests that the policy page doesn't cover (the right answer is "no
  evidence"). All in English, Egyptian Arabic, mixed and Arabizi.
- **Past tickets** (1,500 new): solved customer conversations in the same four styles, with the resolution.

## How the rules are kept right

- Every return in the data, and every "can I return this?" label, is decided by **Team A's own reading of
  the policy** (the checker inside their noon data script), so the new data can't disagree with the
  hand-built data.
- Only values from Team A's shared word list with Team C are used (order statuses, payment methods, item
  conditions, categories), so nothing new needs Team C sign-off.
- `scripts/verify.py` runs 30 checks: the originals are unchanged, ids/emails are unique, totals add up,
  dates are in order and never in the future, refunds go where the payment method says, every label
  matches the policy, and the database row counts match the files.
- Fixed random seed: rebuilding gives the same files every time.

## Folders

| Path | What |
|---|---|
| `corpus/` | noon's public return policy (`noon_return_policy@v1`) and its manifest |
| `seed/` | the hand-built records (11 customers, 56 orders, 10 returns, 67 scenarios, 18 tickets) and `build_noon_mock_data.py`, which regenerates them and holds the reference policy checker |
| `mock/` | the full data the database loads: hand-built records first, then the generated ones |
| `tickets.jsonl` | 1,518 past tickets (hand-built first) |
| `scripts/` | `generate.py` (with `catalog.py`, `texts.py`) builds `mock/` and `tickets.jsonl` from `seed/`; `verify.py` runs 30 checks; `rebuild.sh` does generate, `db build`, verify |
| `benchmark/` | 129 retrieval questions (dev 65 / test 64), written before tuning |
| `synonyms.json` | Arabic/Arabizi to the policy's English wording, for this tenant only |

## Rebuilding

From `rag/`: `bash data/e-commerce/noon_eg/scripts/rebuild.sh`. The seed is fixed (20261008), so the files come out byte-identical.

## Not changed

Team A's schema, the policy text, the retrieval benchmark questions (they were written before tuning and
must stay that way), the word list, and the other shop (`shop_001`).

## Invented on purpose

All people, phones, emails and prices. Shipping fees (30 EGP under 350 in the Noon section, 15 EGP under
150 in Minutes), on top of the 20 EGP cash-on-delivery fee the hand-built data already invented.
