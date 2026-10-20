"""Build the Masr Connect (telco_001) demo database and its companion files.

Run:  python3 generate.py            (writes ../telecom.db and the export files)
      python3 generate.py --small    (about a tenth of the size, for quick checks)

Everything is synthetic. A fixed random seed makes every run produce identical data.
"""
import argparse
import json
import math
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from content_catalog import (BUNDLES, DEVICES, FEMALE_FIRST, GOVERNORATES, LAST, MALE_FIRST, PAYMENT_CHANNELS,
                             PLANS, RECHARGE_CHANNELS, STORES, STREETS, VAS)
from content_conversations import COUNTRIES, FOLLOWUPS, INTENT_WEIGHTS, INTENTS, LANG_WEIGHTS
from content_faq import FAQ
from content_policies import BRAND_AR, BRAND_EN, EFFECTIVE, POLICIES, SOURCES

SEED = 20261008
TENANT = "telco_001"
AS_OF = date(2026, 10, 8)
NOW = "2026-10-08T15:00:00"  # the demo's "current time"; nothing in the data happens after it
HISTORY_START = date(2026, 4, 1)
USAGE_DAYS = 60
RECHARGE_NET = 0.91  # credit after taxes: a 10 EGP card adds 9.10 (Dec 2024 card prices)
SALEFNY_FEES = {5: 1.5, 10: 3, 20: 6, 30: 9, 50: 12.5}  # fee taken from the amount received

OUT = Path(__file__).resolve().parent.parent

GOV_CODES = {"Cairo": "01", "Alexandria": "02", "Port Said": "03", "Suez": "04", "Damietta": "11", "Dakahlia": "12",
             "Sharqia": "13", "Qalyubia": "14", "Kafr El Sheikh": "15", "Gharbia": "16", "Monufia": "17", "Beheira": "18",
             "Ismailia": "19", "Giza": "21", "Beni Suef": "22", "Faiyum": "23", "Minya": "24", "Asyut": "25",
             "Sohag": "26", "Qena": "27", "Aswan": "28", "Luxor": "29", "Red Sea": "31", "South Sinai": "35"}

SCHEMA = """
-- Masr Connect (telco_001) synthetic telecom data. SQLite dialect; plain types so it ports to Postgres.
CREATE TABLE tenant (tenant_id TEXT PRIMARY KEY, brand_en TEXT, brand_ar TEXT, as_of TEXT, currency TEXT, note TEXT);

CREATE TABLE plans (plan_id TEXT PRIMARY KEY, plan_type TEXT NOT NULL, name_en TEXT, name_ar TEXT, monthly_fee REAL,
  data_mb INTEGER, minutes INTEGER, sms INTEGER, speed_mbps INTEGER, credit_limit REAL, min_term_months INTEGER, renew_code TEXT);
CREATE TABLE bundles (bundle_id TEXT PRIMARY KEY, kind TEXT, name_en TEXT, name_ar TEXT, price REAL, validity_days INTEGER,
  data_mb INTEGER, minutes INTEGER, roaming_zone TEXT, ussd_code TEXT);
CREATE TABLE vas_services (vas_id TEXT PRIMARY KEY, name_en TEXT, name_ar TEXT, daily_fee REAL);
CREATE TABLE devices (device_id TEXT PRIMARY KEY, name TEXT, price REAL);
CREATE TABLE stores (store_id TEXT PRIMARY KEY, name TEXT, governorate TEXT, city TEXT);

CREATE TABLE customers (customer_id TEXT PRIMARY KEY, national_id TEXT UNIQUE, nid_last4 TEXT, id_type TEXT,
  first_name_en TEXT, last_name_en TEXT, full_name_ar TEXT, gender TEXT, birth_date TEXT, governorate TEXT, city TEXT,
  address TEXT, email TEXT, contact_msisdn TEXT, preferred_language TEXT, segment TEXT, kyc_status TEXT,
  customer_since TEXT, status TEXT, marketing_opt_out INTEGER, is_demo INTEGER DEFAULT 0, demo_note TEXT);

CREATE TABLE accounts (account_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  account_type TEXT NOT NULL, bill_day INTEGER, credit_limit REAL, outstanding_balance REAL, status TEXT,
  opened_at TEXT, payment_method TEXT, contract_end TEXT);

CREATE TABLE lines (line_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(account_id),
  customer_id TEXT NOT NULL REFERENCES customers(customer_id), msisdn TEXT UNIQUE, line_type TEXT, plan_id TEXT REFERENCES plans(plan_id),
  status TEXT, status_reason TEXT, activated_at TEXT, last_recharge_at TEXT, last_activity_at TEXT,
  prepaid_balance REAL, iccid TEXT, imsi TEXT, puk TEXT, sim_type TEXT, network_gen TEXT, roaming_enabled INTEGER,
  ported_in INTEGER, home_landline TEXT, service_address TEXT, exchange_name TEXT, salefny_outstanding REAL DEFAULT 0);

CREATE TABLE line_bundles (subscription_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), bundle_id TEXT REFERENCES bundles(bundle_id),
  subscribed_at TEXT, expires_at TEXT, auto_renew INTEGER, status TEXT, data_mb_total INTEGER, data_mb_used INTEGER,
  minutes_total INTEGER, minutes_used INTEGER, channel TEXT, price REAL);

CREATE TABLE vas_subscriptions (vas_sub_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), vas_id TEXT REFERENCES vas_services(vas_id),
  subscribed_at TEXT, unsubscribed_at TEXT, channel TEXT, customer_consent TEXT, status TEXT);

CREATE TABLE transactions (txn_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), account_id TEXT, customer_id TEXT,
  txn_type TEXT NOT NULL, created_at TEXT NOT NULL, amount REAL NOT NULL, net_credit REAL, currency TEXT DEFAULT 'EGP',
  channel TEXT, reference TEXT, related_id TEXT, status TEXT, description TEXT, counterparty_msisdn TEXT, is_duplicate INTEGER DEFAULT 0);

CREATE TABLE invoices (invoice_id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(account_id), customer_id TEXT,
  period_start TEXT, period_end TEXT, issued_at TEXT, due_date TEXT, subtotal REAL, taxes REAL, total REAL,
  amount_paid REAL, status TEXT, paid_at TEXT);
CREATE TABLE invoice_items (item_id TEXT PRIMARY KEY, invoice_id TEXT REFERENCES invoices(invoice_id), line_id TEXT,
  item_type TEXT, description TEXT, amount REAL);

CREATE TABLE usage_daily (line_id TEXT REFERENCES lines(line_id), usage_date TEXT, data_mb REAL, voice_onnet_min REAL,
  voice_offnet_min REAL, intl_min REAL, sms INTEGER, roaming_data_mb REAL, roaming_country TEXT, PRIMARY KEY (line_id, usage_date));

CREATE TABLE device_installments (plan_id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(account_id), line_id TEXT,
  device_id TEXT REFERENCES devices(device_id), imei TEXT, start_date TEXT, months INTEGER, monthly_amount REAL,
  paid_installments INTEGER, missed_installments INTEGER, device_locked INTEGER, status TEXT);

CREATE TABLE sim_events (event_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), event_type TEXT, created_at TEXT,
  store_id TEXT, channel TEXT, fee REAL, old_iccid TEXT, new_iccid TEXT, verified_by TEXT, note TEXT);

CREATE TABLE port_requests (port_id TEXT PRIMARY KEY, msisdn TEXT, customer_id TEXT, direction TEXT, other_operator TEXT,
  requested_at TEXT, switch_date TEXT, status TEXT, reject_reason TEXT, fee REAL);

CREATE TABLE network_outages (outage_id TEXT PRIMARY KEY, governorate TEXT, city TEXT, service TEXT, started_at TEXT,
  ended_at TEXT, status TEXT, cause TEXT, compensation_eligible INTEGER);

CREATE TABLE technician_appointments (appointment_id TEXT PRIMARY KEY, line_id TEXT REFERENCES lines(line_id), reason TEXT,
  scheduled_date TEXT, window TEXT, status TEXT, created_at TEXT, fee REAL, notes TEXT);

CREATE TABLE complaints (complaint_id TEXT PRIMARY KEY, customer_id TEXT, line_id TEXT, category TEXT, subcategory TEXT,
  channel TEXT, created_at TEXT, sla_due TEXT, resolved_at TEXT, status TEXT, priority TEXT, escalated_to TEXT,
  resolution TEXT, refund_amount REAL, ntra_referral INTEGER);

CREATE TABLE refunds (refund_id TEXT PRIMARY KEY, customer_id TEXT, line_id TEXT, complaint_id TEXT, reason TEXT, amount REAL,
  method TEXT, requested_at TEXT, approved_by TEXT, status TEXT, processed_at TEXT, related_txn_id TEXT);

CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, customer_id TEXT, line_id TEXT, channel TEXT, language TEXT,
  started_at TEXT, intent TEXT, secondary_intent TEXT, identity_verified INTEGER, outcome TEXT, escalation_reason TEXT,
  complaint_id TEXT, csat INTEGER, frustration TEXT, turns INTEGER);
CREATE TABLE conversation_messages (message_id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(conversation_id),
  seq INTEGER, sender TEXT, sent_at TEXT, text TEXT, citation TEXT);

CREATE TABLE policy_documents (document_id TEXT, version TEXT, title_en TEXT, title_ar TEXT, effective_date TEXT, current INTEGER,
  PRIMARY KEY (document_id, version));
CREATE TABLE policy_sections (passage_id TEXT PRIMARY KEY, document_id TEXT, version TEXT, section_no INTEGER,
  title_en TEXT, title_ar TEXT, text_en TEXT, text_ar TEXT, keywords TEXT, source_ids TEXT);
CREATE TABLE policy_sources (source_id TEXT PRIMARY KEY, description TEXT, url TEXT);
CREATE TABLE faq (faq_id TEXT PRIMARY KEY, category TEXT, question_en TEXT, question_ar TEXT, question_arabizi TEXT,
  answer_en TEXT, answer_ar TEXT, citation TEXT REFERENCES policy_sections(passage_id));
CREATE TABLE business_rules (rule_id TEXT PRIMARY KEY, action TEXT, rule_json TEXT);

CREATE INDEX ix_accounts_customer ON accounts(customer_id);
CREATE INDEX ix_lines_customer ON lines(customer_id);
CREATE INDEX ix_lines_account ON lines(account_id);
CREATE INDEX ix_txn_line_time ON transactions(line_id, created_at);
CREATE INDEX ix_txn_type ON transactions(txn_type);
CREATE INDEX ix_invoices_account ON invoices(account_id);
CREATE INDEX ix_items_invoice ON invoice_items(invoice_id);
CREATE INDEX ix_bundles_line ON line_bundles(line_id);
CREATE INDEX ix_complaints_customer ON complaints(customer_id);
CREATE INDEX ix_conv_customer ON conversations(customer_id);
CREATE INDEX ix_msgs_conv ON conversation_messages(conversation_id);
"""


def dt(d, h=None, m=None, rng=None):
    h = rng.randint(7, 23) if h is None else h
    m = rng.randint(0, 59) if m is None else m
    return datetime(d.year, d.month, d.day, h, m, rng.randint(0, 59) if rng else 0).isoformat(timespec="seconds")


def wchoice(rng, pairs):
    items, weights = zip(*pairs)
    return rng.choices(items, weights=weights)[0]


class Gen:
    def __init__(self, scale):
        self.rng = random.Random(SEED)
        self.scale = scale
        self.rows = {}
        self.msisdns = set()
        self.nids = set()
        self.counters = {}
        self.plans = {p[0]: p for p in PLANS}
        self.bundles = {b[0]: b for b in BUNDLES}

    def nid(self, prefix, width=7):
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}{self.counters[prefix]:0{width}d}"

    def add(self, table, row):
        self.rows.setdefault(table, []).append(row)

    # ---------- identity helpers ----------
    def national_id(self, birth, gov, male, last4=None):
        while True:
            century = "2" if birth.year < 2000 else "3"
            serial = f"{self.rng.randint(0, 999):03d}"
            gdigit = str(self.rng.choice([1, 3, 5, 7, 9] if male else [0, 2, 4, 6, 8]))
            body = f"{century}{birth:%y%m%d}{GOV_CODES[gov]}{serial}{gdigit}"
            nid = body + str(self.rng.randint(1, 9))
            if last4:
                nid = nid[:10] + last4
            if nid not in self.nids:
                self.nids.add(nid)
                return nid

    def msisdn(self, fixed=None):
        if fixed:
            self.msisdns.add(fixed)
            return fixed
        while True:
            prefix = wchoice(self.rng, [("010", 0.4), ("011", 0.2), ("012", 0.25), ("015", 0.15)])
            n = prefix + f"{self.rng.randint(0, 99_999_999):08d}"
            if n not in self.msisdns:
                self.msisdns.add(n)
                return n

    def iccid(self):
        return "8920" + "07" + f"{self.rng.randint(0, 10**13 - 1):013d}"

    def person(self, male=None):
        male = self.rng.random() < 0.52 if male is None else male
        fe, fa = self.rng.choice(MALE_FIRST if male else FEMALE_FIRST)
        fae, faa = self.rng.choice(MALE_FIRST)
        le, la = self.rng.choice(LAST)
        return male, fe, f"{fae} {le}" if self.rng.random() < 0.3 else le, f"{fa} {faa} {la}"

    def place(self):
        gov = wchoice(self.rng, [(g, v[1]) for g, v in GOVERNORATES.items()])
        city = self.rng.choice(GOVERNORATES[gov][0])
        return gov, city

    # ---------- catalog ----------
    def catalog(self):
        self.add("tenant", (TENANT, BRAND_EN, BRAND_AR, AS_OF.isoformat(), "EGP",
                            "Synthetic demo data. Fictional brand, fictional people; numbers and prices are invented."))
        for p in PLANS:
            self.add("plans", p)
        for b in BUNDLES:
            self.add("bundles", b)
        for v in VAS:
            self.add("vas_services", v)
        for d in DEVICES:
            self.add("devices", d)
        for s in STORES:
            self.add("stores", s)
        for doc_id, ver, ten, tar, sections in POLICIES:
            self.add("policy_documents", (doc_id, ver, ten, tar, EFFECTIVE, 1))
            for i, (se, sa, te, ta, kw, src) in enumerate(sections, 1):
                self.add("policy_sections", (f"{doc_id}@{ver}#s{i}", doc_id, ver, i, se, sa, te, ta, json.dumps(kw, ensure_ascii=False),
                                             json.dumps(src)))
        for sid, (desc, url) in SOURCES.items():
            self.add("policy_sources", (sid, desc, url))
        passage_ids = {r[0] for r in self.rows["policy_sections"]}
        for i, f in enumerate(FAQ, 1):
            assert f[6] in passage_ids, f[6]
            self.add("faq", (f"FAQ-{i:03d}", *f))
        for r in RULES:
            self.add("business_rules", (r["rule_id"], r["action"], json.dumps(r, ensure_ascii=False)))

    # ---------- customers, accounts, lines ----------
    def make_customer(self, spec=None):
        spec = spec or {}
        rng = self.rng
        cid = spec.get("customer_id") or self.nid("TC-", 6)
        male, fe, le, ar = self.person(spec.get("male"))
        if "name_en" in spec:
            fe, le = spec["name_en"].split(" ", 1)
            ar = spec["name_ar"]
        gov, city = spec.get("gov") or self.place()
        if "gov" in spec:
            gov, city = spec["gov"]
        age = rng.choices([rng.randint(18, 25), rng.randint(26, 40), rng.randint(41, 60), rng.randint(61, 80)], [0.25, 0.4, 0.25, 0.1])[0]
        birth = date(AS_OF.year - age, rng.randint(1, 12), rng.randint(1, 28))
        foreign = not spec and rng.random() < 0.015
        nid = (f"P{rng.randint(10**7, 10**8 - 1)}" if foreign else self.national_id(birth, gov, male, spec.get("nid_last4")))
        lang = spec.get("lang") or wchoice(rng, list(LANG_WEIGHTS.items()))
        since = spec.get("since") or (HISTORY_START - timedelta(days=rng.randint(0, 365 * 12)) if rng.random() < 0.85
                                      else HISTORY_START + timedelta(days=rng.randint(0, (AS_OF - HISTORY_START).days - 5)))
        email = f"{fe.lower()}.{le.split()[-1].lower().replace('-', '')}{rng.randint(1, 999)}@example.com" if rng.random() < 0.6 else None
        segment = spec.get("segment") or wchoice(rng, [("mass", 0.7), ("youth", 0.15), ("premium", 0.1), ("business", 0.05)])
        row = dict(customer_id=cid, national_id=nid, nid_last4=nid[-4:], id_type="passport" if foreign else "national_id",
                   first_name_en=fe, last_name_en=le, full_name_ar=ar, gender="M" if male else "F", birth_date=birth.isoformat(),
                   governorate=gov, city=city, address=f"{rng.randint(1, 180)} {rng.choice(STREETS)}, {city}",
                   email=email, contact_msisdn=None, preferred_language=lang, segment=segment, kyc_status="verified",
                   customer_since=since.isoformat(), status="active", marketing_opt_out=int(rng.random() < 0.12),
                   is_demo=int(bool(spec)), demo_note=spec.get("note"))
        return row

    def make_account(self, cust, atype, spec=None):
        spec = spec or {}
        rng = self.rng
        opened = max(date.fromisoformat(cust["customer_since"]), spec.get("opened", date.fromisoformat(cust["customer_since"])))
        acc = dict(account_id=self.nid("AC-", 7), customer_id=cust["customer_id"], account_type=atype,
                   bill_day=spec.get("bill_day", rng.choice([1, 8, 15, 22])) if atype not in ("prepaid", "control") else None, credit_limit=None,
                   outstanding_balance=0.0, status="active", opened_at=opened.isoformat(),
                   payment_method=wchoice(rng, PAYMENT_CHANNELS) if atype != "prepaid" else None, contract_end=None)
        return acc

    def make_line(self, cust, acc, plan_id, spec=None):
        spec = spec or {}
        rng = self.rng
        plan = self.plans[plan_id]
        activated = spec.get("activated") or max(date.fromisoformat(acc["opened_at"]),
                                                  date.fromisoformat(acc["opened_at"]) + timedelta(days=rng.randint(0, 400)))
        if activated > AS_OF - timedelta(days=3):
            activated = AS_OF - timedelta(days=rng.randint(3, 30))
        home = plan[1] == "home_internet"
        line = dict(line_id=self.nid("LN-", 7), account_id=acc["account_id"], customer_id=cust["customer_id"],
                    msisdn=self.msisdn(spec.get("msisdn")) if not home else None, line_type=plan[1], plan_id=plan_id,
                    status=spec.get("status", "active"), status_reason=spec.get("status_reason"),
                    activated_at=activated.isoformat(), last_recharge_at=None, last_activity_at=None,
                    prepaid_balance=None, iccid=None if home else self.iccid(),
                    imsi=None if home else "60204" + f"{rng.randint(0, 10**10 - 1):010d}",
                    puk=None if home else f"{rng.randint(0, 10**8 - 1):08d}",
                    sim_type=None if home else wchoice(rng, [("physical", 0.92), ("esim", 0.08)]),
                    network_gen=None if home else wchoice(rng, [("4G", 0.8), ("5G", 0.12), ("3G", 0.08)]),
                    roaming_enabled=0, ported_in=int(not home and rng.random() < 0.07),
                    home_landline=spec.get("landline") or ("0" + rng.choice(["2", "3", "40", "50", "88"]) + f"{rng.randint(1000000, 9999999)}") if home else None,
                    service_address=cust["address"] if home else None,
                    exchange_name=f"{cust['city']} Exchange {rng.randint(1, 4)}" if home else None,
                    salefny_outstanding=spec.get("salefny", 0))
        if plan[1] in ("prepaid", "control"):
            line["prepaid_balance"] = spec.get("balance", round(rng.choice([0, 0.5, 2.3, 5, 8.75, 12, 18.4, 25, 40, 63.2, 110]) * rng.uniform(0.5, 1.5), 2))
        if plan[1] == "postpaid":
            acc["credit_limit"] = spec.get("credit_limit", plan[9])
            # roaming is free once 4 bills are paid (about 4 months on the network), earlier only with a deposit
            line["roaming_enabled"] = int(activated < AS_OF - timedelta(days=125) or (not spec.get("demo") and rng.random() < 0.05))
        if plan[1] == "home_internet" and plan[10]:
            acc["contract_end"] = (activated + timedelta(days=30 * plan[10])).isoformat()
        return line

    def population(self):
        rng = self.rng
        n_customers = int(12000 * self.scale)
        self.customers, self.accounts, self.lines = [], [], []
        for spec in DEMO:
            self.build_customer(spec)
        while len(self.customers) < n_customers:
            self.build_customer(None)

    def build_customer(self, spec):
        rng = self.rng
        cust = self.make_customer(spec["customer"] if spec else None)
        self.customers.append(cust)
        acc_specs = spec["accounts"] if spec else self.random_portfolio(cust)
        for aspec in acc_specs:
            acc = self.make_account(cust, aspec["type"], aspec)
            self.accounts.append(acc)
            for lspec in aspec["lines"]:
                line = self.make_line(cust, acc, lspec["plan"], lspec)
                line["_spec"] = lspec
                self.lines.append(line)
                if cust["contact_msisdn"] is None and line["msisdn"]:
                    cust["contact_msisdn"] = line["msisdn"]

    def random_portfolio(self, cust):
        rng = self.rng
        seg = cust["segment"]
        accs = []
        r = rng.random()
        if seg == "business":
            accs.append({"type": "postpaid", "lines": [{"plan": "POST-BIZ-300"} for _ in range(rng.randint(1, 4))]})
        elif seg == "premium" or r < 0.18:
            accs.append({"type": "postpaid", "lines": [{"plan": rng.choice(["POST-RED-200", "POST-RED-350", "POST-RED-550", "POST-RED-900"])}
                                                       for _ in range(rng.choices([1, 2, 3], [0.75, 0.2, 0.05])[0])]})
        else:
            plan = wchoice(rng, [("PRE-BASIC", 0.35), ("PRE-SECONDS", 0.2), ("PRE-YOUTH", 0.15 if seg == "youth" else 0.03),
                                 ("CTL-50", 0.12), ("CTL-90", 0.1), ("CTL-140", 0.05)])
            ptype = self.plans[plan][1]
            accs.append({"type": ptype, "lines": [{"plan": plan}] + ([{"plan": "PRE-BASIC"}] if rng.random() < 0.12 else [])})
        if rng.random() < 0.22:
            accs.append({"type": "home_internet", "lines": [{"plan": wchoice(rng, [("HOME-ADSL-140", 0.35), ("HOME-VDSL-250", 0.35),
                                                                                     ("HOME-VDSL-400", 0.2), ("HOME-FIBER-1TB", 0.1)])}]})
        # statuses for random lines
        for a in accs:
            for l in a["lines"]:
                x = rng.random()
                if x < 0.03:
                    l["status"], l["status_reason"] = "suspended", "unpaid_invoice" if a["type"] == "postpaid" else "customer_request"
                elif x < 0.05 and a["type"] in ("prepaid", "control"):
                    l["status"], l["status_reason"] = "inactive", "receive_only_no_paid_use_90_days"
                elif x < 0.06:
                    l["status"], l["status_reason"] = "terminated", "customer_request"
        return accs

    # ---------- activity ----------
    def activity(self):
        rng = self.rng
        self.txns_by_line = {}
        for line in self.lines:
            spec = line["_spec"]
            start = max(HISTORY_START, date.fromisoformat(line["activated_at"]))
            end = AS_OF
            if line["status"] == "terminated":
                end = AS_OF - timedelta(days=rng.randint(10, 120))
            if line["status"] == "inactive":
                end = AS_OF - timedelta(days=spec.get("idle_days", rng.randint(91, 104)))
            if end <= start:
                end = start + timedelta(days=1)
            t = line["line_type"]
            if t in ("prepaid", "control"):
                self.prepaid_activity(line, start, end)
            if line["status"] != "terminated" and t != "home_internet":
                self.bundle_activity(line, start, end)
                self.usage(line, end)
            if t == "home_internet":
                self.usage(line, end)
            if line["status"] == "terminated":
                pass
            for extra in spec.get("extras", []):
                getattr(self, "extra_" + extra["kind"])(line, extra)
        for acc in self.accounts:
            if acc["account_type"] in ("postpaid", "home_internet"):
                self.billing(acc)

    def txn(self, line, ttype, when, amount, **kw):
        if when > NOW:
            when = f"2026-10-08T{self.rng.randint(0, 14):02d}:{self.rng.randint(0, 59):02d}:00"
        row = dict(txn_id=self.nid("TX-", 8), line_id=line["line_id"], account_id=line["account_id"], customer_id=line["customer_id"],
                   txn_type=ttype, created_at=when, amount=round(amount, 2), net_credit=kw.get("net_credit"), currency="EGP",
                   channel=kw.get("channel"), reference=kw.get("reference"), related_id=kw.get("related_id"),
                   status=kw.get("status", "succeeded"), description=kw.get("description"),
                   counterparty_msisdn=kw.get("counterparty"), is_duplicate=kw.get("is_duplicate", 0))
        self.add("transactions", row)
        return row

    def prepaid_activity(self, line, start, end):
        rng = self.rng
        intensity = rng.choice([4, 6, 8, 10, 14, 21])
        d = start
        last = None
        while d < end:
            d += timedelta(days=max(1, int(rng.expovariate(1 / intensity))))
            if d >= end:
                break
            amount = rng.choice([5, 10, 10, 15, 20, 25, 30, 50, 50, 70, 100, 100, 150, 200, 300])
            channel = wchoice(rng, RECHARGE_CHANNELS)
            when = dt(d, rng=rng)
            status = "succeeded" if rng.random() > 0.004 else "failed"
            self.txn(line, "recharge", when, amount, net_credit=round(amount * RECHARGE_NET, 2) if status == "succeeded" else 0,
                     channel=channel, reference=f"RC{rng.randint(10**9, 10**10 - 1)}", status=status,
                     description=f"Recharge {amount} EGP via {channel}")
            last = when
            if rng.random() < 0.05:
                to = self.msisdn()
                self.msisdns.discard(to)
                amt = rng.choice([5, 10, 20, 50])
                fee = max(0.2, round(amt * 0.02, 2))
                self.txn(line, "balance_transfer_out", dt(d, rng=rng), -amt - fee, channel="ussd", counterparty=to,
                         description=f"Balance transfer {amt} EGP + {fee:.2f} fee (2%)")
            if rng.random() < 0.03:
                amt = rng.choice([5, 10, 10, 20, 30, 50])
                fee = SALEFNY_FEES[amt]
                self.txn(line, "salefny_loan", dt(d, rng=rng), amt, net_credit=amt - fee, channel="ussd",
                         description=f"Salefny {amt} EGP, {fee} EGP fee taken on receipt")
                self.txn(line, "salefny_repayment", dt(d + timedelta(days=rng.randint(1, 6)), rng=rng), -amt,
                         channel="auto", description="Salefny repaid from next recharge")
        line["last_recharge_at"] = last
        line["last_activity_at"] = dt(end - timedelta(days=1), rng=rng) if line["status"] == "active" else (last or line["activated_at"])

    def bundle_activity(self, line, start, end):
        rng = self.rng
        t = line["line_type"]
        if t == "postpaid" and rng.random() < 0.6:
            pool = ["NET-30", "NET-55", "NET-100", "SOCIAL-25", "ROAM-GULF-7", "ROAM-EU-7"]
        elif t == "postpaid":
            return
        else:
            pool = ["NET-15", "NET-30", "NET-55", "NET-100", "NET-200", "NET-DAY-5", "NET-WEEK-20", "SOCIAL-10", "SOCIAL-25",
                    "MIX-40", "MIX-75", "MIN-20", "MIN-45"]
        demo = line["_spec"].get("demo")
        if demo:
            end = min(end, AS_OF - timedelta(days=40))
        fav = rng.choice(pool)
        d = start + timedelta(days=rng.randint(0, 10))
        while d < end:
            b = self.bundles[fav if rng.random() < 0.8 else rng.choice(pool)]
            when = min(dt(d, rng=rng), NOW)
            exp = d + timedelta(days=b[5])
            active = exp > AS_OF and line["status"] == "active"
            used_frac = min(1.0, rng.betavariate(2, 2) * ((AS_OF - d).days / b[5] if active else 1.2))
            sub_id = self.nid("SUB-", 8)
            self.add("line_bundles", (sub_id, line["line_id"], b[0], when, dt(exp, rng=rng), int(b[5] >= 30 and rng.random() < 0.7),
                                      "active" if active else "expired", b[6], int(b[6] * used_frac), b[7], int(b[7] * used_frac),
                                      wchoice(rng, [("app", 0.5), ("ussd", 0.35), ("store", 0.05), ("chat", 0.1)]), b[4]))
            self.txn(line, "bundle_purchase", when, -b[4], related_id=sub_id, channel="balance" if t != "postpaid" else "invoice",
                     description=f"{b[2]} subscription")
            d = exp + timedelta(days=rng.randint(0, 6))
        # value-added services
        if t != "postpaid" and not demo and rng.random() < 0.12:
            v = rng.choice(VAS)
            sub_d = AS_OF - timedelta(days=rng.randint(5, 150))
            consent = wchoice(rng, [("ussd_opt_in", 0.5), ("sms_keyword", 0.3), ("web_click", 0.2)])
            stopped = rng.random() < 0.4
            unsub = sub_d + timedelta(days=rng.randint(3, 60)) if stopped else None
            if unsub and unsub >= AS_OF:
                unsub = None
            vid = self.nid("VS-", 7)
            self.add("vas_subscriptions", (vid, line["line_id"], v[0], sub_d.isoformat(), unsub.isoformat() if unsub else None,
                                           consent, "explicit" if consent != "web_click" else "unclear", "unsubscribed" if unsub else "active"))
            stop = unsub or AS_OF
            day = max(sub_d, AS_OF - timedelta(days=30))
            while day < stop:
                self.txn(line, "vas_charge", dt(day, 4, 0, rng), -v[3], related_id=vid, channel="balance", description=f"{v[1]} daily fee")
                day += timedelta(days=1)

    def usage(self, line, end):
        rng = self.rng
        home = line["line_type"] == "home_internet"
        base_data = rng.lognormvariate(math.log(9000 if home else 350), 0.6)
        base_voice = 0 if home else rng.lognormvariate(math.log(12), 0.8)
        roam_days = set()
        if not home and line["roaming_enabled"] and rng.random() < 0.08:
            s = rng.randint(5, USAGE_DAYS - 10)
            roam_days = set(range(s, s + rng.randint(3, 12)))
            country = rng.choice(COUNTRIES)
        rows = []
        for i in range(USAGE_DAYS):
            d = AS_OF - timedelta(days=USAGE_DAYS - i)
            if d > end or d < date.fromisoformat(line["activated_at"]):
                continue
            if rng.random() < (0.02 if home else 0.12):
                continue
            wk = 1.25 if d.weekday() in (4, 5) else 1.0
            roaming = i in roam_days
            rows.append((line["line_id"], d.isoformat(), round(base_data * wk * rng.uniform(0.3, 1.8), 1),
                         0 if home else round(base_voice * rng.uniform(0, 1.6), 1), 0 if home else round(base_voice * 0.4 * rng.uniform(0, 1.8), 1),
                         0 if home else (round(rng.uniform(0, 8), 1) if rng.random() < 0.03 else 0),
                         0 if home else int(rng.random() < 0.2) * rng.randint(1, 6),
                         round(rng.uniform(20, 400), 1) if roaming else 0, country if roaming else None))
        self.rows.setdefault("usage_daily", []).extend(rows)

    def billing(self, acc):
        rng = self.rng
        lines = [l for l in self.lines if l["account_id"] == acc["account_id"]] if not hasattr(self, "_lines_by_acc") else self._lines_by_acc[acc["account_id"]]
        if not lines:
            return
        spec = lines[0]["_spec"]
        opened = date.fromisoformat(acc["opened_at"])
        bill_day = acc["bill_day"]
        # cycles from HISTORY_START to AS_OF
        d = date(HISTORY_START.year, HISTORY_START.month, bill_day)
        late_next = 0.0
        unpaid_months = spec.get("unpaid_cycles", 0)
        if not spec.get("demo") and lines[0]["status"] == "suspended":
            unpaid_months = 2
        cycles = []
        while d <= AS_OF:
            if d > opened + timedelta(days=30):
                cycles.append(d)
            m = d.month + 1
            d = date(d.year + (m > 12), (m - 1) % 12 + 1, bill_day)
        payer = wchoice(rng, [("always", 0.75), ("late", 0.18), ("partial", 0.07)])
        for idx, issued in enumerate(cycles):
            inv_id = f"INV-{issued:%y%m}-{acc['account_id'][3:]}"
            items, subtotal = [], 0.0
            pstart = issued - timedelta(days=30)
            for l in lines:
                if l["status"] == "terminated":
                    continue
                plan = self.plans[l["plan_id"]]
                items.append(("plan_fee", f"{plan[2]} monthly fee", plan[4], l["line_id"]))
                if l["line_type"] == "postpaid":
                    if rng.random() < 0.35:
                        items.append(("extra_usage", "Out-of-bundle usage", round(rng.uniform(5, 120), 2), l["line_id"]))
                    if rng.random() < 0.04:
                        items.append(("roaming", "Roaming usage", round(rng.uniform(80, 900), 2), l["line_id"]))
            for extra in spec.get("invoice_items", []):
                if idx == len(cycles) - 1:
                    items.append((extra["type"], extra["description"], extra["amount"], lines[0]["line_id"]))
            for inst in self.installments_by_acc.get(acc["account_id"], []):
                if date.fromisoformat(inst[5]) <= issued:
                    items.append(("device_installment", f"Device installment ({inst[3]})", inst[7], inst[2]))
            if late_next:
                items.append(("late_fee", "Late payment fee", late_next, lines[0]["line_id"]))
            subtotal = round(sum(i[2] for i in items), 2)
            taxes = round(subtotal * 0.14, 2)
            total = round(subtotal + taxes, 2)
            due = issued + timedelta(days=15)
            is_last = idx >= len(cycles) - unpaid_months
            if is_last and unpaid_months:
                paid, status, paid_at = 0.0, "overdue" if due < AS_OF else "open", None
            elif due > AS_OF:
                paid, status, paid_at = 0.0, "open", None
            else:
                mode = payer if rng.random() < 0.9 else "always"
                if mode == "always":
                    pd = issued + timedelta(days=rng.randint(0, 14))
                    paid, status = total, "paid"
                elif mode == "late":
                    pd = due + timedelta(days=rng.randint(1, 20))
                    paid, status = total, "paid_late"
                else:
                    pd = issued + timedelta(days=rng.randint(3, 14))
                    paid, status = round(total * rng.uniform(0.3, 0.8), 2), "partially_paid"
                paid_at = dt(min(pd, AS_OF), rng=rng)
                late_next = 25.0 if status != "paid" else 0.0
                self.txn(lines[0], "invoice_payment", paid_at, paid, related_id=inv_id, channel=acc["payment_method"],
                         reference=f"PY{rng.randint(10**9, 10**10 - 1)}", description=f"Payment for {inv_id}")
            self.add("invoices", (inv_id, acc["account_id"], acc["customer_id"], pstart.isoformat(), issued.isoformat(), issued.isoformat(),
                                  due.isoformat(), subtotal, taxes, total, paid, status, paid_at))
            for j, it in enumerate(items, 1):
                self.add("invoice_items", (f"{inv_id}-{j:02d}", inv_id, it[3], it[0], it[1], it[2]))
            if status in ("overdue", "open", "partially_paid"):
                acc["outstanding_balance"] = round(acc["outstanding_balance"] + total - paid, 2)
        if lines[0]["status"] == "suspended" and lines[0]["status_reason"] == "unpaid_invoice":
            acc["status"] = "suspended"

    def installments(self):
        rng = self.rng
        self.installments_by_acc = {}
        for acc in self.accounts:
            if acc["account_type"] != "postpaid":
                continue
            lines = self._lines_by_acc[acc["account_id"]]
            spec = lines[0]["_spec"]
            if "installment" not in spec and (spec.get("demo") or rng.random() > 0.15):
                continue
            ins = spec.get("installment", {})
            dev = self.bundles_dev[ins.get("device", rng.choice([d[0] for d in DEVICES[:8]]))]
            months = ins.get("months", rng.choice([12, 24]))
            start = ins.get("start", max(date.fromisoformat(acc["opened_at"]), AS_OF - timedelta(days=rng.randint(30, 700))))
            elapsed = max(0, min(months, (AS_OF - start).days // 30))
            missed = ins.get("missed", 1 if rng.random() < 0.06 else 0)
            row = (self.nid("DI-", 6), acc["account_id"], lines[0]["line_id"], dev[0], f"35{rng.randint(10**12, 10**13 - 1)}",
                   start.isoformat(), months, round(dev[2] / months, 2), max(0, elapsed - missed), missed, int(missed >= 2),
                   "completed" if elapsed >= months and not missed else ("locked" if missed >= 2 else "active"))
            self.add("device_installments", row)
            self.installments_by_acc.setdefault(acc["account_id"], []).append(row)
            if not acc["contract_end"]:
                acc["contract_end"] = (start + timedelta(days=30 * months)).isoformat()

    # ---------- service events ----------
    def service_events(self):
        rng = self.rng
        live = [l for l in self.lines if l["msisdn"] and l["status"] != "terminated"]
        for l in rng.sample(live, int(len(live) * 0.06)):
            et = wchoice(rng, [("sim_replacement", 0.55), ("esim_issue", 0.15), ("sim_upgrade_4g", 0.15), ("line_suspend_lost", 0.15)])
            st = rng.choice(STORES)
            new = self.iccid()
            self.add("sim_events", (self.nid("SE-", 6), l["line_id"], et, dt(AS_OF - timedelta(days=rng.randint(1, 180)), rng=rng),
                                    None if et == "line_suspend_lost" else st[0], "chat" if et == "line_suspend_lost" else "store",
                                    0 if et in ("line_suspend_lost", "esim_issue") else 5, l["iccid"],
                                    None if et == "line_suspend_lost" else new, "national_id_in_person" if et != "line_suspend_lost" else "msisdn_plus_nid4", None))
        ops = ["Operator A", "Operator B", "Operator C"]
        for l in [l for l in live if l["ported_in"]]:
            self.add("port_requests", (self.nid("PR-", 6), l["msisdn"], l["customer_id"], "in", rng.choice(ops), l["activated_at"],
                                       (date.fromisoformat(l["activated_at"]) + timedelta(days=1)).isoformat(), "completed", None, 0))
        for l in rng.sample(live, int(len(live) * 0.012)):
            age = (AS_OF - date.fromisoformat(l["activated_at"])).days
            req = AS_OF - timedelta(days=rng.randint(0, 120))
            status, reason = ("rejected", "owned_less_than_4_months") if age < 120 else wchoice(rng, [(("completed", None), 0.7), (("cancelled_by_customer", None), 0.15), (("rejected", "latest_bill_unpaid"), 0.15)])
            self.add("port_requests", (self.nid("PR-", 6), l["msisdn"], l["customer_id"], "out", rng.choice(ops), req.isoformat(),
                                       (req + timedelta(days=1)).isoformat() if status == "completed" else None, status, reason, 0))
        # outages
        govs = list(GOVERNORATES)
        for i in range(int(140 * max(self.scale, 0.3))):
            gov = wchoice(rng, [(g, v[1]) for g, v in GOVERNORATES.items()])
            city = rng.choice(GOVERNORATES[gov][0])
            start = datetime.combine(AS_OF - timedelta(days=rng.randint(0, 185)), datetime.min.time()) + timedelta(hours=rng.randint(0, 23))
            hours = rng.choice([1, 2, 3, 5, 8, 12, 20, 30, 50, 72])
            end = start + timedelta(hours=hours)
            status = "resolved" if end < datetime.combine(AS_OF, datetime.min.time()) else "ongoing"
            self.add("network_outages", (self.nid("OUT-", 5), gov, city, rng.choice(["mobile_data", "mobile_voice", "home_internet", "all"]),
                                         start.isoformat(), end.isoformat() if status == "resolved" else None, status,
                                         rng.choice(["fiber_cut", "power_failure", "equipment_fault", "planned_maintenance", "weather"]),
                                         int(hours > 24)))
        homes = [l for l in self.lines if l["line_type"] == "home_internet" and l["status"] == "active"]
        for l in rng.sample(homes, int(len(homes) * 0.15)):
            sd = AS_OF + timedelta(days=rng.randint(-120, 5))
            status = "scheduled" if sd >= AS_OF else wchoice(rng, [("completed", 0.8), ("no_show_customer", 0.08), ("cancelled", 0.07), ("rescheduled", 0.05)])
            reason = rng.choice(["installation", "fault_no_sync", "slow_speed", "router_replacement", "relocation"])
            self.add("technician_appointments", (self.nid("TA-", 6), l["line_id"], reason, sd.isoformat(),
                                                 rng.choice(["09:00-13:00", "13:00-17:00", "17:00-21:00"]), status,
                                                 min(dt(min(sd, AS_OF) - timedelta(days=rng.randint(1, 5)), rng=rng), NOW),
                                                 75.0 if reason.startswith("fault") and rng.random() < 0.2 else 0.0, None))

    # ---------- support history ----------
    def support_history(self):
        rng = self.rng
        n = int(9000 * self.scale)
        intents = list(INTENT_WEIGHTS.items())
        live = [l for l in self.lines if l["status"] != "terminated"]
        sla_days = {"billing": 3, "home_internet": 5, "roaming": 5}
        for _ in range(n):
            intent = wchoice(rng, intents)
            spec = INTENTS[intent]
            line = rng.choice(live)
            if intent in ("home_internet_down", "slow_internet"):
                homes = [l for l in self.lines_home]
                line = rng.choice(homes)
            cust_lang = self.cust_by_id[line["customer_id"]]["preferred_language"]
            lang = cust_lang if rng.random() < 0.8 else wchoice(rng, list(LANG_WEIGHTS.items()))
            started = datetime.combine(AS_OF - timedelta(days=rng.randint(0, 180)), datetime.min.time()) + timedelta(hours=rng.randint(7, 23), minutes=rng.randint(0, 59))
            if started.isoformat() > "2026-10-08T14:30:00":
                started -= timedelta(days=1)
            fill = dict(amount=rng.choice([10, 20, 50, 55, 100, 200, 260, 450]), bundle=rng.choice(BUNDLES[:13])[2],
                        msisdn=line["msisdn"] or "01000000000", country=rng.choice(COUNTRIES), days=rng.randint(1, 5))
            outcome_kind, p = spec["outcome"]
            outcome = outcome_kind if rng.random() < p else rng.choice(["escalated", "abandoned", "resolved"])
            frustration = wchoice(rng, [("low", 0.7), ("medium", 0.2), ("high", 0.1)])
            if intent == "complaint_escalation":
                frustration = "high"
            conv_id = self.nid("CV-", 7)
            msgs = []
            t = started

            def say(sender, text, cite=None):
                nonlocal t
                t += timedelta(seconds=rng.randint(8, 120))
                msgs.append((f"{conv_id}-{len(msgs) + 1:02d}", conv_id, len(msgs) + 1, sender, t.isoformat(timespec="seconds"), text, cite))

            say("customer", rng.choice(spec["openers"][lang]).format(**fill))
            reply_lang = "ar" if lang == "mixed" else lang
            say("agent", spec["reply"][reply_lang].format(**fill), spec["citation"])
            verified = 0
            if intent not in ("greeting", "recharge_tax_question", "roaming_activation", "sim_swap_request", "cancel_line",
                              "call_log_request", "third_party_request", "fraud_report", "complaint_escalation") and line["msisdn"]:
                say("customer", f"{line['msisdn']} - {self.cust_by_id[line['customer_id']]['nid_last4']}")
                verified = 1
            if frustration == "high" and outcome != "resolved":
                say("customer", FOLLOWUPS["frustrated"][lang])
            if outcome == "escalated" and rng.random() < 0.5:
                say("customer", FOLLOWUPS["human"][lang])
            if outcome == "resolved" and rng.random() < 0.6:
                say("customer", FOLLOWUPS["thanks"][lang])
            esc_reason = None
            if outcome == "escalated":
                esc_reason = {"fraud_report": "mandatory_risk", "complaint_escalation": "customer_requested_human",
                              "bill_dispute": "needs_approval", "roaming_charges": "needs_approval"}.get(intent,
                              wchoice(rng, [("customer_requested_human", 0.35), ("tool_failure", 0.15), ("policy_denied", 0.2),
                                            ("low_confidence", 0.15), ("high_frustration", 0.15)]))
            complaint_id = None
            if outcome == "escalated" or (intent in ("wrong_deduction", "bill_dispute", "home_internet_down", "slow_internet", "vas_unsubscribe") and rng.random() < 0.6):
                complaint_id = self.complaint(line, intent, spec["category"], started, outcome, sla_days)
            csat = None if outcome == "abandoned" or rng.random() < 0.55 else (
                rng.choice([4, 5, 5, 5]) if outcome == "resolved" else rng.choice([1, 2, 3, 3, 4]))
            self.add("conversations", (conv_id, line["customer_id"], line["line_id"], wchoice(rng, [("whatsapp", 0.5), ("app_chat", 0.3), ("web_chat", 0.15), ("facebook", 0.05)]),
                                       lang, started.isoformat(timespec="seconds"), intent, None, verified, outcome, esc_reason,
                                       complaint_id, csat, frustration, len(msgs)))
            self.rows.setdefault("conversation_messages", []).extend(msgs)
        # complaints from other channels
        for _ in range(int(3000 * self.scale)):
            line = rng.choice(live)
            intent = wchoice(rng, [("wrong_deduction", 3), ("bill_dispute", 3), ("home_internet_down", 2), ("slow_internet", 2),
                                   ("vas_unsubscribe", 2), ("roaming_charges", 1), ("recharge_missing", 2), ("line_suspended", 1)])
            started = datetime.combine(AS_OF - timedelta(days=rng.randint(1, 180)), datetime.min.time()) + timedelta(hours=rng.randint(8, 22))
            self.complaint(line, intent, INTENTS[intent]["category"], started, "resolved", sla_days,
                           channel=wchoice(rng, [("hotline_1414", 0.6), ("store", 0.25), ("email", 0.1), ("app", 0.05)]))

    def complaint(self, line, intent, category, started, outcome, sla_days, channel="chat"):
        rng = self.rng
        cid = self.nid("CMP-", 7)
        sla = started + timedelta(days=sla_days.get(category, 7))
        open_ = started.date() > AS_OF - timedelta(days=sla_days.get(category, 7)) and rng.random() < 0.6
        resolved = None if open_ else started + timedelta(hours=rng.randint(2, 24 * (sla_days.get(category, 7) + 3)))
        if resolved and resolved.isoformat() > NOW:
            resolved, open_ = None, True
        status = "open" if open_ else ("resolved" if resolved <= sla else "resolved_late")
        refund = None
        if not open_ and intent in ("wrong_deduction", "vas_unsubscribe", "bill_dispute", "bundle_refund", "roaming_charges") and rng.random() < 0.55:
            refund = round(rng.choice([5.5, 10, 15, 22.5, 30, 55, 100, 150, 260, 650]) * (1 if intent != "vas_unsubscribe" else 0.4), 2)
            approver = "agent" if refund <= 200 else "supervisor"
            rid = self.nid("RF-", 6)
            method = "balance" if line["line_type"] in ("prepaid", "control") else "invoice_credit"
            self.add("refunds", (rid, line["customer_id"], line["line_id"], cid, intent, refund, method, started.isoformat(timespec="seconds"),
                                 approver, "processed", (resolved or started).isoformat(timespec="seconds"), None))
            self.txn(line, "refund", (resolved or started).isoformat(timespec="seconds"), refund, related_id=rid, channel=method,
                     description=f"Refund for {cid}")
        ntra = int(status == "resolved_late" and rng.random() < 0.08)
        self.add("complaints", (cid, line["customer_id"], line["line_id"], category, intent, channel, started.isoformat(timespec="seconds"),
                                sla.isoformat(timespec="seconds"), resolved.isoformat(timespec="seconds") if resolved else None, status,
                                "urgent" if intent == "fraud_report" else rng.choice(["normal", "normal", "high"]),
                                "senior_team" if outcome == "escalated" else None,
                                None if open_ else rng.choice(["explained_to_customer", "refund_issued", "fault_fixed", "adjustment_rejected", "technician_visit"]) if not refund else "refund_issued",
                                refund, ntra))
        return cid

    # ---------- demo extras (used by DEMO specs) ----------
    def extra_recharge(self, line, e):
        self.txn(line, "recharge", e["at"], e["amount"], net_credit=round(e["amount"] * RECHARGE_NET, 2) if e.get("status", "succeeded") == "succeeded" else 0,
                 channel=e.get("channel", "fawry"), reference=e.get("reference", "RC9000000001"), status=e.get("status", "succeeded"),
                 description=f"Recharge {e['amount']} EGP via {e.get('channel', 'fawry')}")
        line["last_recharge_at"] = e["at"]

    def extra_bundle(self, line, e):
        b = self.bundles[e["bundle"]]
        sub_id = e["subscription_id"]
        for k in range(e.get("times", 1)):
            if k == 0:
                self.add("line_bundles", (sub_id, line["line_id"], b[0], e["at"], e["expires"], 1, "active", b[6], int(b[6] * e["used"]), b[7], 0, e.get("channel", "ussd"), b[4]))
            self.txn(line, "bundle_purchase", e["at"], -b[4], related_id=sub_id, channel="balance", description=f"{b[2]} subscription",
                     is_duplicate=int(k > 0), reference=f"BP-{sub_id}-{k + 1}")

    def extra_vas(self, line, e):
        self.add("vas_subscriptions", (e["vas_sub_id"], line["line_id"], e["vas"], e["since"], None, e["channel"], e["consent"], "active"))
        fee = next(v[3] for v in VAS if v[0] == e["vas"])
        d = max(date.fromisoformat(e["since"]), AS_OF - timedelta(days=45))
        while d < AS_OF:
            self.txn(line, "vas_charge", dt(d, 4, 0, self.rng), -fee, related_id=e["vas_sub_id"], channel="balance", description="Daily fee")
            d += timedelta(days=1)

    def extra_outage(self, line, e):
        c = self.cust_by_id[line["customer_id"]]
        self.add("network_outages", (e["outage_id"], c["governorate"], c["city"], e["service"], e["start"], None, "ongoing", e["cause"], 1))

    def extra_appointment(self, line, e):
        self.add("technician_appointments", (e["appointment_id"], line["line_id"], e["reason"], e["date"], e["window"], "scheduled", e["created"], 0.0, None))

    def extra_charge(self, line, e):
        self.txn(line, e["type"], e["at"], e["amount"], channel=e.get("channel", "balance"), description=e["description"],
                 related_id=e.get("related_id"), is_duplicate=e.get("is_duplicate", 0))

    # ---------- write ----------
    def run(self):
        self.catalog()
        self.bundles_dev = {d[0]: d for d in DEVICES}
        self.population()
        self.cust_by_id = {c["customer_id"]: c for c in self.customers}
        self._lines_by_acc = {}
        for l in self.lines:
            self._lines_by_acc.setdefault(l["account_id"], []).append(l)
        self.lines_home = [l for l in self.lines if l["line_type"] == "home_internet" and l["status"] != "terminated"]
        self.installments()
        self.activity()
        self.service_events()
        self.support_history()
        for c in self.customers:
            self.add("customers", c)
        for a in self.accounts:
            self.add("accounts", a)
        for l in self.lines:
            l.pop("_spec", None)
            self.add("lines", l)


def write_db(gen, path):
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for table, rows in gen.rows.items():
        if not rows:
            continue
        if isinstance(rows[0], dict):
            cols = list(rows[0].keys())
            data = [tuple(r[c] for c in cols) for r in rows]
            con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", data)
        else:
            con.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
    con.commit()
    return con


from demo_and_rules import DEMO, RULES  # noqa: E402  (kept at the bottom: it is data, and it is long)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true")
    ap.add_argument("--out", default=str(OUT / "telecom.db"))
    args = ap.parse_args()
    gen = Gen(0.1 if args.small else 1.0)
    gen.run()
    path = Path(args.out)
    con = write_db(gen, path)
    (OUT / "schema.sql").write_text(SCHEMA.strip() + "\n", encoding="utf-8", newline="\n")
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t:28s}{n:>10,}")
    con.close()


if __name__ == "__main__":
    main()
