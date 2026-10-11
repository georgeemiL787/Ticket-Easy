"""Add the network map to telecom.db: apply the migrations, then build sites, cells, core, exchanges, access ports,
home routers, line bindings, outage links and the 10 network-fault demo customers (TC-D021..TC-D030).

Run after generate.py and before export.py:  python3 net_generate.py [--db path/to/telecom.db]
A fixed seed makes every run on the same telecom.db produce the same network. Running it twice is safe: migrations
already applied are skipped, and the backfill is skipped once the network demo customers exist.
"""
import argparse
import json
import random
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from demo_network import CORE_NFS, DEMO_NET, ETAS, OUTAGE_D029, REGION_OF, REGIONS
from generate import GOV_CODES, NOW

SEED = 20261010
OUT = Path(__file__).resolve().parent.parent
MIGRATIONS = OUT / "migrations"
BASELINE = "2026-10-01T00:00:00"  # when the inventory was last reconciled; elements untouched since then show this
SCHEMA_MARK = "\n-- ======== network troubleshooting (migrations/001_network_automation.sql) ========\n"

ALARM_CODE = {"fiber_cut": "link_down", "weather": "link_down", "power_failure": "power_failure",
              "equipment_fault": "equipment_fault", "planned_maintenance": "planned_maintenance"}
DETECTED_BY = {"fiber_cut": "customer_reports", "weather": "customer_reports", "power_failure": "automation",
               "equipment_fault": "automation", "planned_maintenance": "manual"}
CELL_VENDORS = [("Ericsson", "AIR 6449", "23.Q4"), ("Nokia", "AirScale", "24R1"), ("Huawei", "AAU5613", "V100R021")]
CPE_MODELS = {"gpon": [("Huawei", "HG8145V5"), ("ZTE", "F670L")], "vdsl2": [("Huawei", "DN8245V"), ("ZTE", "H168N")],
              "adsl2+": [("TP-Link", "TD-W9970"), ("ZTE", "H108N")]}


def slug(s):
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")


def ts(s):
    return s if s is None or len(s) > 10 else s + "T00:00:00"


def plus(s, minutes):
    return (datetime.fromisoformat(s) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


class Net:
    def __init__(self, con):
        self.con = con
        self.rng = random.Random(SEED)
        self.counters = {}
        self.elements = {}  # element_id -> row dict, written in insertion order (parents before children)
        self.rows = {"network_sites": [], "network_links": [], "line_network_bindings": [], "network_alarms": []}
        self.on_element = {}  # element_id -> lines currently bound to it
        self.uplink = {}  # element_id -> link_id towards the core / gateway
        for line_id, eid in con.execute("SELECT line_id, element_id FROM line_network_bindings WHERE valid_to IS NULL"):
            self.on_element.setdefault(eid, set()).add(line_id)

    def nid(self, prefix, width, start=1):
        self.counters[prefix] = self.counters.get(prefix, start - 1) + 1
        return f"{prefix}{self.counters[prefix]:0{width}d}"

    def q(self, sql, *a):
        return self.con.execute(sql, a).fetchall()

    def element(self, eid, etype, name, site, parent=None, vendor=None, model=None, firmware=None, managed="ems", ref=None,
                shared=1, attrs=None):
        self.elements[eid] = dict(element_id=eid, element_type=etype, name=name, site_id=site, parent_element_id=parent, vendor=vendor,
                                  model=model, firmware=firmware, status="active", status_changed_at=BASELINE, managed_by=managed,
                                  external_ref=ref, shared=shared, attributes_json=json.dumps(attrs or {}, ensure_ascii=False))
        return eid

    def link(self, a, b, ltype, capacity, uplink=False):
        lid = self.nid("LK-", 6)
        self.rows["network_links"].append(dict(link_id=lid, a_element_id=a, b_element_id=b, link_type=ltype, capacity_mbps=capacity,
                                               status="active"))
        if uplink:
            self.uplink[a] = self.rows["network_links"][-1]
        return lid

    def bind(self, line_id, eid, role, frm, to, source):
        self.rows["line_network_bindings"].append(dict(binding_id=self.nid("BD-", 7), line_id=line_id, element_id=eid, role=role,
                                                       valid_from=frm, valid_to=to, source=source))
        if to is None:
            self.on_element.setdefault(eid, set()).add(line_id)

    def alarm(self, eid, severity, code, message, raised, cleared=None):
        assert raised <= NOW
        self.rows["network_alarms"].append(dict(alarm_id=self.nid("AL-", 6), element_id=eid, severity=severity, alarm_code=code,
                                                message=message, raised_at=raised, acknowledged_at=plus(raised, 12) if plus(raised, 12) <= NOW else None,
                                                cleared_at=cleared))

    def fail(self, eid, status, at):
        self.elements[eid].update(status=status, status_changed_at=at)
        if eid in self.uplink:
            self.uplink[eid]["status"] = status

    # ---------- H7. demo customers, one per fault type ----------
    def demo_customers(self):
        credit = dict(self.q("SELECT plan_id, credit_limit FROM plans"))
        for i, d in enumerate(DEMO_NET):
            gov, city = d["gov"]
            n = d["customer_id"][3:]  # D021
            birth = f"{1972 + 3 * i}-{1 + i:02d}-{5 + 2 * i:02d}"
            nid = f"2{birth[2:4]}{birth[5:7]}{birth[8:10]}{GOV_CODES[gov]}{i % 10}{d['nid_last4']}"
            first, last = d["name_en"].split(" ", 1)
            self.con.execute("INSERT INTO customers (customer_id, national_id, nid_last4, id_type, first_name_en, last_name_en, full_name_ar, gender, "
                             "birth_date, governorate, city, address, email, contact_msisdn, preferred_language, segment, kyc_status, customer_since, "
                             "status, marketing_opt_out, is_demo, demo_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (d["customer_id"], nid, d["nid_last4"], "national_id", first, last, d["name_ar"], "M" if d["male"] else "F", birth,
                              gov, city, f"{10 + 7 * i} El Tahrir St, {city}", None, None, d["lang"], "mass", "verified", "2018-03-01", "active", 0, 1,
                              d["note"]))
            atype = d["account_type"]
            postpaid = atype == "postpaid"
            self.con.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (f"AC-{n}", d["customer_id"], atype, 1 if atype != "prepaid" else None, credit[d["plan"]] if postpaid else None,
                              0.0, "active", "2018-03-01", None if atype == "prepaid" else "app", None))
            last_activity = {"TC-D024": "2026-10-08T06:11:40", "TC-D026": "2026-10-08T13:19:55", "TC-D029": "2026-10-08T10:29:30"}
            rng = self.rng
            self.con.execute("INSERT INTO lines (line_id, account_id, customer_id, msisdn, line_type, plan_id, status, status_reason, activated_at, "
                             "last_recharge_at, last_activity_at, prepaid_balance, iccid, imsi, puk, sim_type, network_gen, roaming_enabled, ported_in, "
                             "home_landline, service_address, exchange_name, salefny_outstanding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (f"LN-{n}", f"AC-{n}", d["customer_id"], d["msisdn"], atype, d["plan"], "active", None, "2023-03-15",
                              None if postpaid else "2026-10-01T18:20:00", last_activity.get(d["customer_id"], "2026-10-08T14:05:00"),
                              None if postpaid else 35.0, "892007" + f"{rng.randint(0, 10**13 - 1):013d}", "60204" + f"{rng.randint(0, 10**10 - 1):010d}",
                              f"{rng.randint(0, 10**8 - 1):08d}", "physical", d["network_gen"], int(postpaid), 0, None, None, None, 0))
            if d["fault"] == "quota":
                self.con.execute("INSERT INTO line_bundles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (f"SUB-{n}", f"LN-{n}", "NET-55", "2026-09-25T10:00:00", "2026-10-25T10:00:00", 1, "active", 3500, 3500, 0, 0, "ussd", 55.0))

    # ---------- H1. sites ----------
    def sites(self):
        existing = {(g, c): s for s, g, c in self.q("SELECT site_id, governorate, city FROM network_sites")}
        self.site_of = {}
        for gov, city in self.q("SELECT DISTINCT governorate, city FROM customers ORDER BY 1, 2"):
            sid = existing.get((gov, city)) or f"SITE-{slug(gov)[:3]}-{slug(city)}"
            if (gov, city) not in existing:
                self.rows["network_sites"].append(dict(site_id=sid, name=city if city == gov else f"{gov} {city}", governorate=gov, city=city))
            self.site_of[(gov, city)] = sid

    # ---------- H2. core per region, backhaul and cells per site ----------
    def mobile(self):
        demo_cells = [r[0] for r in self.q("SELECT element_id FROM network_elements WHERE element_type IN ('gnb_cell','enb_cell') ORDER BY 1")]
        self.counters["NE-CELL-"] = len(demo_cells)
        self.counters["NE-CORE-"] = len(CORE_NFS)
        self.core = {"GC": {nf: f"NE-CORE-{i:03d}" for i, nf in enumerate(CORE_NFS, 1)}}
        self.hub, self.bng = {"GC": "SITE-CAI-DT"}, {}
        for code, (name, govs) in REGIONS.items():
            if code != "GC":
                gov = next(g for g in govs if any(k[0] == g for k in self.site_of))
                self.hub[code] = min(s for (g, _), s in self.site_of.items() if g == gov)
                self.core[code] = {nf: self.element(self.nid("NE-CORE-", 3), "core_nf", f"{nf}-{code}", self.hub[code], vendor="Nokia",
                                                    model=f"Cloud {nf}", firmware="24.3", attrs={"nf": nf, "region": code})
                                   for nf in CORE_NFS}
            self.bng[code] = self.element(f"NE-BNG-{code}", "bng", f"BNG-{code}", self.hub[code], vendor="Juniper", model="MX960",
                                          firmware="23.4R1", attrs={"region": name})
        self.bh_of, self.cells_of = {}, {}
        for (gov, city), sid in sorted(self.site_of.items()):
            region = REGION_OF[gov]
            bh = self.element(self.nid("NE-BH-", 3), "backhaul_node", f"BH {city}", sid, vendor="Ciena", model="5164", firmware="10.8",
                              attrs={"region": region})
            self.link(bh, self.core[region]["UPF"], "fiber", 10000, uplink=True)
            self.bh_of[sid] = bh
            if sid == "SITE-CAI-DT":  # the demo cluster already has its seven cells
                self.cells_of[sid] = [(c, "gnb_cell") for c in demo_cells]
                continue
            cells, n = [], self.rng.randint(3, 7)
            vendor = self.rng.choice(CELL_VENDORS)
            for k in range(n):
                gnb = k == 0 or self.rng.random() < 0.3
                macro = k < (n + 1) // 2
                eid = self.nid("NE-CELL-", 3)
                self.element(eid, "gnb_cell" if gnb else "enb_cell", f"{'gNB' if gnb else 'eNB'}-{eid[-3:]}", sid, parent=bh,
                             vendor=vendor[0], model=vendor[1], firmware=vendor[2],
                             attrs={"cell_class": "macro" if macro else "micro", "band": "n78" if gnb else self.rng.choice(["B1", "B3", "B20"]),
                                    "pci": self.rng.randint(0, 503), "capacity_mbps": (1500 if gnb else 300) if macro else (400 if gnb else 150)})
                self.link(eid, bh, "backhaul", 1000 if macro else 400, uplink=True)
                if cells:
                    self.link(cells[-1][0], eid, "X2", 1000)
                cells.append((eid, "gnb_cell" if gnb else "enb_cell"))
            self.cells_of[sid] = cells

    def pick_cell(self, sid, gen, first=False):
        cells = self.cells_of[sid]
        pool = [c for c, t in cells if (t == "gnb_cell") == (gen == "5G")] or [c for c, _ in cells]
        return pool[0] if first else self.rng.choice(pool)

    # ---------- H4/H5. exchanges, access ports, home routers ----------
    def home(self):
        speed = dict(self.q("SELECT plan_id, speed_mbps FROM plans"))
        self.exch_of, self.exchs_in, self.ports_of = {}, {}, {}
        for gov, city, ex in self.q("SELECT DISTINCT c.governorate, c.city, l.exchange_name FROM lines l JOIN customers c USING(customer_id) "
                                    "WHERE l.exchange_name IS NOT NULL ORDER BY 1, 2, 3"):
            bng = self.bng[REGION_OF[gov]]
            eid = self.element(self.nid("NE-EXCH-", 3), "exchange", ex, self.site_of[(gov, city)], parent=bng, vendor="Huawei",
                               model="MA5800", firmware="V100R022", attrs={"technologies": ["adsl2+", "vdsl2", "gpon"]})
            self.link(eid, bng, "fiber", 10000, uplink=True)
            self.exch_of[ex] = eid
            self.exchs_in.setdefault((gov, city), []).append(eid)
        for line_id, plan, ex, status, activated, last_act, landline in self.q(
                "SELECT line_id, plan_id, exchange_name, status, activated_at, last_activity_at, home_landline FROM lines "
                "WHERE line_type = 'home_internet' ORDER BY line_id"):
            exch = self.exch_of[ex]
            site = self.elements[exch]["site_id"]
            tech = "gpon" if "FIBER" in plan else "adsl2+" if "ADSL" in plan else "vdsl2"
            ports = self.ports_of.setdefault(exch, [])
            port = self.element(self.nid("NE-PORT-", 5), "olt_port" if tech == "gpon" else "dslam_port",
                                f"{'OLT' if tech == 'gpon' else 'DSLAM'}-1/{len(ports) // 48 + 1}/{len(ports) % 48 + 1}", site, parent=exch,
                                vendor="Huawei", model="MA5800", shared=0, attrs={"technology": tech, "profile_mbps": speed[plan]})
            ports.append(port)
            vendor, model = self.rng.choice(CPE_MODELS[tech])
            cpe = self.nid("NE-CPE-", 5)
            self.element(cpe, "cpe", f"Home router {landline}", site, parent=port, vendor=vendor, model=model, firmware="V5R20",
                         managed="acs", ref=f"MC{cpe[-5:]}{landline[-4:]}", shared=0, attrs={"wifi_bands": ["2.4GHz", "5GHz"]})
            frm, to = self.window(activated, last_act, status, mobile=False)
            self.bind(line_id, port, "access_port", frm, to, "inventory")
            self.bind(line_id, cpe, "cpe", frm, to, "inventory")

    @staticmethod
    def window(activated, last_act, status, mobile):
        start, last = ts(activated), ts(last_act)
        if status == "terminated":
            return start, last if last and last >= start else start
        return (last if mobile and last and last >= start else start), None

    # ---------- H3. serving cell for every mobile line ----------
    def serving_cells(self):
        bound = {r[0] for r in self.q("SELECT line_id FROM line_network_bindings WHERE role = 'serving_cell' AND valid_to IS NULL")}
        fixed = {f"LN-{d['customer_id'][3:]}": self.pick_cell(self.site_of[d["gov"]], d["network_gen"], first=True) for d in DEMO_NET}
        for line_id, gov, city, gen, status, activated, last_act in self.q(
                "SELECT l.line_id, c.governorate, c.city, l.network_gen, l.status, l.activated_at, l.last_activity_at "
                "FROM lines l JOIN customers c USING(customer_id) WHERE l.line_type != 'home_internet' ORDER BY l.line_id"):
            if line_id in bound:
                continue
            cell = fixed.get(line_id) or self.pick_cell(self.site_of[(gov, city)], gen)
            frm, to = self.window(activated, last_act, status, mobile=True)
            self.bind(line_id, cell, "serving_cell", frm, to, "network_event")
        return fixed

    # ---------- demo faults ----------
    def faults(self, fixed):
        self.d029_cell = None
        for d in DEMO_NET:
            cell = fixed[f"LN-{d['customer_id'][3:]}"]
            sid = self.site_of[d["gov"]]
            if d["fault"] == "cell_down":
                self.fail(cell, "down", "2026-10-08T13:20:00")
                self.alarm(cell, "critical", "cell_down", "Cell out of service: N2/S1 connection lost", "2026-10-08T13:20:00")
            elif d["fault"] == "backhaul":
                bh = self.bh_of[sid]
                self.fail(bh, "degraded", "2026-10-08T09:45:00")
                self.alarm(bh, "major", "backhaul_degraded", "Backhaul packet loss 7.5%, latency 180 ms", "2026-10-08T09:45:00")
            elif d["fault"] == "core":
                smf = self.core[REGION_OF[d["gov"][0]]]["SMF"]
                self.fail(smf, "degraded", "2026-10-08T11:05:00")
                self.alarm(smf, "major", "session_setup_failures", "PDU session setup success rate 41%", "2026-10-08T11:05:00")
            elif d["fault"] == "outage":
                self.d029_cell = cell
                self.fail(cell, "degraded", OUTAGE_D029["started_at"])
                self.alarm(cell, "major", "data_path_failure", "User-plane failure: data sessions on this cell are failing",
                           OUTAGE_D029["started_at"])
                o = OUTAGE_D029
                self.con.execute("INSERT INTO network_outages (outage_id, governorate, city, service, started_at, ended_at, status, cause, "
                                 "compensation_eligible) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (o["outage_id"], *d["gov"], o["service"], o["started_at"], None, "ongoing", o["cause"], 1))

    # ---------- H6. outages -> equipment ----------
    def outages(self):
        d007_exch = self.exch_of[self.q("SELECT exchange_name FROM lines WHERE home_landline = '0502234007'")[0][0]]
        updates, links = [], []
        for oid, gov, city, service, started, ended, status, cause in self.q(
                "SELECT outage_id, governorate, city, service, started_at, ended_at, status, cause FROM network_outages ORDER BY outage_id"):
            sid = self.site_of[(gov, city)]
            bh, cells = self.bh_of[sid], [c for c, _ in self.cells_of[sid]]
            exchs = self.exchs_in.get((gov, city), [])
            if oid == OUTAGE_D029["outage_id"]:
                root, els = self.d029_cell, [self.d029_cell]
            elif service == "home_internet":
                root = d007_exch if oid == "OUT-D007" else self.rng.choice(exchs)
                els = [root] + self.ports_of.get(root, [])
            elif service.startswith("mobile"):
                root, els = bh, [bh] + cells
            else:  # all services
                root, els = bh, [bh] + cells + exchs + [p for e in exchs for p in self.ports_of.get(e, [])]
            affected = len(set().union(*(self.on_element.get(e, set()) for e in els)))
            updates.append((root, DETECTED_BY[cause], affected, ETAS.get(oid), oid))
            links += [(oid, e) for e in els]
            what = f"{service.replace('_', ' ')} outage ({cause.replace('_', ' ')}) in {city}"
            if oid == OUTAGE_D029["outage_id"]:
                continue  # its cell and alarm were set by faults()
            if status == "ongoing":
                self.fail(root, "down", started)
                self.alarm(root, "critical", ALARM_CODE[cause], what, started)
            else:
                self.alarm(root, "info" if cause == "planned_maintenance" else "major", ALARM_CODE[cause], what, started, cleared=ended)
        return updates, links

    def write(self):
        def insert(table, rows):
            if rows:
                cols = list(rows[0])
                self.con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                     [tuple(r[c] for c in cols) for r in rows])
        insert("network_sites", self.rows["network_sites"])
        insert("network_elements", list(self.elements.values()))
        for t in ("network_links", "line_network_bindings", "network_alarms"):
            insert(t, self.rows[t])

    def run(self):
        self.demo_customers()
        self.sites()
        self.mobile()
        self.home()
        fixed = self.serving_cells()
        self.faults(fixed)
        updates, links = self.outages()
        self.write()
        self.con.executemany("UPDATE network_outages SET root_element_id = ?, detected_by = ?, affected_lines = ?, eta = ? WHERE outage_id = ?", updates)
        self.con.executemany("INSERT INTO outage_elements VALUES (?, ?)", links)


def migrate(con):
    done = set()
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone():
        done = {v for (v,) in con.execute("SELECT version FROM schema_migrations")}
    for f in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        if f.name[:3] not in done:
            con.executescript(f.read_text(encoding="utf-8"))
            print(f"applied {f.name}")


def write_schema():
    """Append the migration's tables to schema.sql (generate.py rewrites the base part on every build)."""
    path = OUT / "schema.sql"
    base = path.read_text(encoding="utf-8").split(SCHEMA_MARK)[0].rstrip("\n") + "\n"
    skip = re.compile(r"^(PRAGMA |BEGIN;|COMMIT;|INSERT INTO schema_migrations|-- Run:)")
    ddl = [l for l in (MIGRATIONS / "001_network_automation.sql").read_text(encoding="utf-8").splitlines() if not skip.match(l)]
    path.write_text(base + SCHEMA_MARK + "\n".join(ddl).strip() + "\n", encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(OUT / "telecom.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON")
    migrate(con)
    write_schema()
    if con.execute("SELECT 1 FROM customers WHERE customer_id = ?", (DEMO_NET[0]["customer_id"],)).fetchone():
        print("network already generated; nothing to do")
        return
    con.execute("BEGIN")
    Net(con).run()
    con.execute("COMMIT")
    for t in ("network_sites", "network_elements", "network_links", "line_network_bindings", "network_alarms", "outage_elements",
              "root_cause_catalog", "resolution_procedures"):
        print(f"{t:28s}{con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>10,}")
    con.close()


if __name__ == "__main__":
    main()
