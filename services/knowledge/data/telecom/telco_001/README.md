# Telecom demo data: "Masr Connect"

A complete Egyptian mobile and home-internet company so Ticket-Easy can be tried on a second kind of business, next to the clothing shop. The brand and all the people and accounts are made up. The rules follow the real public terms of WE, Vodafone, Orange, e& and the regulator NTRA (see `SOURCES.md`).

## What's inside

**The company and its rules**
- 11 policies, each in English and Arabic: recharge and balance, monthly bills, bundles, refunds, SIM cards, moving your number, roaming, home internet, contracts and devices, complaints, and privacy (`policies/`). Every section links to the real operator or NTRA page it's based on, or says it's the company's own rule.
- 60 frequently asked questions. Each one has the question in English, Egyptian Arabic and Arabizi, an answer in English and Arabic, and a link to the policy section it comes from (`faq.md`).
- 19 business rules the agent must follow, for example "a number can only move to another network after 4 months" or "refunds over 200 EGP need a supervisor".
- The actions a support agent could take (check a balance, refund a charge, suspend a stolen line, and so on), with the risky ones, like a SIM swap or cancelling a line, marked as store-only.

**The customers and their activity** (in `telecom.db`, six months up to 8 Oct 2026)

| What | How many |
|---|---|
| Customers | 12,000 |
| Accounts (prepaid, Control, monthly, home internet) | 14,668 |
| Phone and internet lines | 17,490 |
| Money movements (recharges, bundle purchases, payments, transfers, refunds, service charges) | 367,497 |
| Daily usage records (data, minutes, SMS, roaming) | 815,532 |
| Bundle subscriptions | 87,816 |
| Monthly bills and their line items | 36,641 bills, 66,887 items |
| Past support chats (4 languages) | 9,000 chats, 28,512 messages |
| Complaints and refunds | 5,099 complaints, 1,482 refunds |
| Also | phone installments, SIM replacements, number transfers, network outages, technician visits, 31 stores |

**30 hand-made demo customers** (`TC-D001` to `TC-D030`; the last 10 are network faults, see below). Each one is set up for a specific situation: someone who subscribed to a bundle by mistake an hour ago, someone whose bill is high because of roaming, someone whose line is suspended for unpaid bills, a stolen phone, a duplicated charge above the agent's refund limit, a line too new to move to another network (under 4 months), a new monthly line that needs a deposit before roaming, and so on. Their notes are in the database (`customers.demo_note`).

**43 test conversations** (`scenarios/telco_001/`), about a quarter each in English, Egyptian Arabic, mixed Arabic-English, and Arabizi. Each one says what the customer types and what the agent should do at every step: answer, ask for identity, ask to confirm, carry out the action, refuse, or hand over to a person. They use the demo customers, so the facts they expect match the data. They're marked "pending" because the agent doesn't support telecom yet.

## How it plugs into Ticket-Easy

The files under `fixtures/telco_001/`, `config/tenants/telco_001.json` and `scenarios/telco_001/` follow the same layout as the shop's files in the agent service. Adding telecom later should mostly mean copying them in and adding the telecom actions to the simulated shop system.

The one real difference from the shop is identity. Instead of order number plus phone, a telecom customer proves who they are with their line number (or home landline) plus the last 4 digits of their national ID.

## Rebuilding it

The data comes from a script with a fixed seed, so every run gives exactly the same result:

```
cd scripts
python3 generate.py      # builds telecom.db (about 20 seconds)
python3 net_generate.py  # adds the network map and troubleshooting tables (migrations/), about 1 second
python3 export.py        # writes the policies, FAQ, fixtures and test conversations
python3 verify.py        # 34 consistency checks; all pass
```

**Network troubleshooting.** `migrations/001_network_automation.sql` adds the network map (sites, equipment, links, which equipment serves each line, alarms), the troubleshooting playbook (16 root causes, 12 procedures) and the history tables for investigations and fixes. `002_seed_catalogue_and_demo_cluster.sql` fills the playbook and a 7-cell demo cluster that mirrors TelecomAgent's default network. `net_generate.py` applies both, then builds a site for every city, cells and core per region, a port and home router for every home line, a serving cell for every mobile line, and links each outage to its equipment. It also adds 10 more demo customers, `TC-D021` to `TC-D030`, one per fault type (used-up bundle, stuck data session, outdated line settings, phone not connected, weak signal, cell down, backhaul fault, core fault, known outage, unclear). `fixtures/telco_001/network.json` holds the part of the network around the demo customers, so tests run without the database. The database itself refuses unsafe rows: no change to shared equipment without a person's approval, the same fix never runs twice, and the audit log can't be edited.

`python3 generate.py --small` builds a tenth-size copy for quick tries. The database is SQLite, so it needs no setup. `schema.sql` describes every table and uses plain types, so moving to Postgres later is straightforward.

See `SOURCES.md` for every source, and for which rules are real and which are the company's own choices.
