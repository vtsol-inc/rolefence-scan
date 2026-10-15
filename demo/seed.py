"""Generate the demo dataset: a made-up removals company, "Brightline Movers".

Everything here is invented. Names, emails, phones, addresses and amounts are built from
word lists with a fixed random seed, so the dataset is the same on every machine. Emails use
example.net, phones Ofcom's drama range (+44 7700 900xxx) and bank refs a fake bank code
(GB00TEST...), so none can belong to a real person or account.

Usage (admin connection string from an environment variable, never a flag):

    export DEMO_ADMIN_URI='mongodb://<admin>:<pw>@127.0.0.1:27017/admin'
    python demo/seed.py            # writes database rolefence_demo (drops it first)
    python demo/seed.py --small    # 1/10 of the rows, for quick runs

Built-in traps the scanner should find (see README):
  - orders.supplier_cost is secret for the sales role; price is not, so margin can be derived
  - enquiries.notes is free text that holds personal data
  - enquiries.status and orders.status are bare integer codes
  - supplier_payments.bank_ref is a sensitive value that only finance may see
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

DB_NAME = "rolefence_demo"
SEED = 20261015

FIRST = ["Ava", "Noah", "Mia", "Leo", "Zara", "Omar", "Ivy", "Hugo", "Lina", "Theo",
         "Nora", "Eli", "Sara", "Max", "Ruby", "Adam", "Isla", "Finn", "Maya", "Kai",
         "Aria", "Jude", "Ella", "Sam", "Layla", "Idris", "Rosa", "Tariq", "Nina", "Yusuf"]
LAST = ["Patel", "Okafor", "Lindqvist", "Haddad", "Moreau", "Kowalski", "Nakamura", "Silva",
        "Byrne", "Andersen", "Rahman", "Costa", "Novak", "Ferreira", "Dubois", "Khan",
        "Mensah", "Larsen", "Rossi", "Weber", "Ahmed", "Murphy", "Schmidt", "Ali", "Garcia"]
CITIES = ["Northbridge", "Eastmere", "Saltford", "Weston Vale", "Harrowgate", "Millbrook",
          "Kingsmoor", "Ashby Cross", "Redcliffe", "Fenwick"]
STREETS = ["Mill Lane", "Station Road", "Orchard Close", "High Street", "Chapel Hill",
           "Meadow Way", "Bridge Street", "Park Avenue", "Willow Drive", "Castle Row"]
SUPPLIERS = ["Swift Vans Ltd", "Two Men and a Lorry", "Harbour Haulage", "Vale Removals",
             "Northstar Logistics", "Quickbox Movers", "Fenwick Freight", "Ashby Transport"]
NOTE_SNIPPETS = [
    "Customer prefers morning slot.",
    "Call back on {phone} after 6pm.",
    "Partner's name is {name}; they will be at the new address.",
    "Has a piano, needs 3 crew.",
    "Email {email} bounced once, try again.",
    "Fragile: glass cabinet, wrap twice.",
    "Passport number left at old flat, do not lose box 4.",
    "Landlord contact {name}, {phone}.",
    "Parking permit needed at destination.",
    "Mentioned they are moving for a new job at the hospital.",
]
ENQ_STATUS = {1: "new", 2: "quoted", 3: "booked", 4: "lost", 5: "cancelled"}
ORD_STATUS = {1: "scheduled", 2: "in_progress", 3: "completed", 4: "refunded"}
CHANNELS = ["email", "whatsapp", "phone"]

rng = random.Random(SEED)


def person():
    f, l = rng.choice(FIRST), rng.choice(LAST)
    return {
        "name": f"{f} {l}",
        "email": f"{f.lower()}.{l.lower()}{rng.randint(1, 999)}@example.net",
        "phone": fake_phone(),
    }


def fake_phone():
    # Ofcom's range for drama (07700 900000-900999): never a real number. Two draws as
    # before, so the rest of the dataset does not change.
    rng.randint(100, 999)
    return f"+44 7700 900{rng.randint(100000, 999999) % 1000:03d}"


def fake_bank_ref():
    # Check digits 00 and bank code TEST: not a valid IBAN, cannot be anyone's account.
    rng.randint(10, 99)
    return f"GB00TEST{rng.randint(10**13, 10**14 - 1)}"


def address():
    return f"{rng.randint(1, 180)} {rng.choice(STREETS)}, {rng.choice(CITIES)}"


def when(start: datetime, days: int) -> datetime:
    return start + timedelta(days=rng.randint(0, days), minutes=rng.randint(0, 1439))


def build(scale: float):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    n_cust = int(2000 * scale)
    n_enq = int(3200 * scale)

    customers = []
    for i in range(n_cust):
        p = person()
        customers.append({"_id": i + 1, **p, "city": rng.choice(CITIES),
                          "created_at": when(start, 600)})

    suppliers = [{"_id": i + 1, "name": s, "city": rng.choice(CITIES),
                  "rating": round(rng.uniform(3.0, 5.0), 1)} for i, s in enumerate(SUPPLIERS)]

    enquiries, orders, supplier_payments, payments_received, messages = [], [], [], [], []
    order_no = 0
    for i in range(n_enq):
        c = rng.choice(customers)
        created = when(c["created_at"], 120)
        rooms = rng.randint(1, 6)
        price = rooms * rng.randint(180, 260) + rng.randint(0, 150)
        status = rng.choices([1, 2, 3, 4, 5], weights=[8, 30, 35, 22, 5])[0]
        other = person()
        note = rng.choice(NOTE_SNIPPETS).format(phone=c["phone"], email=c["email"],
                                                name=other["name"])
        enq = {
            "_id": i + 1,
            "ref": f"ENQ-{1000 + i}",
            "customer_id": c["_id"],
            "from_address": address(),
            "to_address": address(),
            "move_date": when(created, 60).date().isoformat(),
            "rooms": rooms,
            "quoted_price": price,
            "status": status,
            "notes": note if rng.random() < 0.6 else "",
            "created_at": created,
        }
        enquiries.append(enq)
        messages.append({"customer_id": c["_id"], "enquiry_id": enq["_id"], "direction": "in",
                         "channel": rng.choice(CHANNELS), "sent_at": created,
                         "body": f"Hi, I need a quote for a {rooms}-room move to {enq['to_address']}."})
        if status == 3:
            order_no += 1
            sup = rng.choice(suppliers)
            cost = int(price * rng.uniform(0.55, 0.85))
            ostatus = rng.choices([1, 2, 3, 4], weights=[20, 5, 70, 5])[0]
            order = {
                "_id": order_no,
                "ref": f"ORD-{5000 + order_no}",
                "enquiry_id": enq["_id"],
                "customer_id": c["_id"],
                "supplier_id": sup["_id"],
                "price": price,
                "supplier_cost": cost,  # secret for sales
                "status": ostatus,
                "move_date": enq["move_date"],
                "created_at": when(created, 3),
            }
            orders.append(order)
            payments_received.append({"order_id": order_no, "customer_id": c["_id"],
                                      "amount": price if ostatus != 4 else price // 2,
                                      "method": rng.choice(["card", "transfer", "cash"]),
                                      "received_at": when(order["created_at"], 10)})
            if ostatus in (3, 4):
                supplier_payments.append({"order_id": order_no, "supplier_id": sup["_id"],
                                          "amount": cost,
                                          "bank_ref": fake_bank_ref(),
                                          "paid_at": when(order["created_at"], 20)})
            messages.append({"customer_id": c["_id"], "enquiry_id": enq["_id"], "direction": "out",
                             "channel": "email", "sent_at": order["created_at"],
                             "body": f"Your move is booked for {enq['move_date']} with {sup['name']}."})

    return {
        "customers": customers,
        "suppliers": suppliers,
        "enquiries": enquiries,
        "orders": orders,
        "supplier_payments": supplier_payments,
        "payments_received": payments_received,
        "messages": messages,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--small", action="store_true", help="write 1/10 of the rows")
    args = ap.parse_args()

    uri = os.environ.get("DEMO_ADMIN_URI")
    if not uri:
        print("DEMO_ADMIN_URI is not set (export it in the shell; never pass it as a flag)", file=sys.stderr)
        return 2
    if "127.0.0.1" not in uri and "localhost" not in uri:
        print("refusing: the demo only writes to a local MongoDB", file=sys.stderr)
        return 2

    data = build(0.1 if args.small else 1.0)
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.drop_database(DB_NAME)
    db = client[DB_NAME]
    for name, docs in data.items():
        if docs:
            db[name].insert_many(docs)
        print(f"{name:20s} {len(docs):6d}")
    db.enquiries.create_index("customer_id")
    db.orders.create_index("customer_id")
    db.orders.create_index("enquiry_id")
    db.messages.create_index("customer_id")
    print(f"wrote database {DB_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
