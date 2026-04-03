"""
Fashion Marketplace — 3-Month Event Simulation
Sends to Amplitude HTTP API v2 + dumps full JSON log.

Planted mistakes (flagged in event metadata for audit validation):
  M1 — price sent as string instead of float
  M2 — missing required property (currency or order_id)
  M3 — inconsistent product_id mid-funnel (changes between PD and Product Added)
  M4 — Order Completed fires without Checkout Started in same session
  M5 — discount_pct mathematically wrong vs actual price/compare_at_price
  M6 — is_first_order: true for users who already have prior orders
"""
#simulate.py

import json, uuid, random, time, math, os, sys
from datetime import datetime, timedelta
from copy import deepcopy
from collections import defaultdict

import requests
from faker import Faker

fake = Faker("en_IN")
random.seed(42)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
AMPLITUDE_API_KEY = os.getenv("AMPLITUDE_API_KEY", "64e42405bb0b320e9ec09d091a3593b2")
AMPLITUDE_URL     = "https://api2.amplitude.com/2/httpapi"
SEND_TO_AMPLITUDE = AMPLITUDE_API_KEY != "YOUR_API_KEY_HERE"

SIM_START   = datetime(2026, 3, 1)
SIM_END     = datetime(2026, 4, 1)
NUM_USERS   = 200
BATCH_SIZE  = 20        # Amplitude max batch
OUTPUT_FILE = "simulated_events.json"
LOG_FILE    = "mistake_log.json"

# How many events to send to Amplitude (None = send all ~40k)
# Keeps laptop safe while guaranteeing all 6 mistake types are represented
SEND_LIMIT        = 2000
MISTAKES_PER_CODE = 3   # guarantee at least this many of each M1-M6

# Mistake injection rates
MISTAKE_RATES = {
    "M1_price_as_string":          0.07,
    "M2_missing_required_prop":    0.05,
    "M3_inconsistent_product_id":  0.08,
    "M4_order_without_checkout":   0.06,
    "M5_wrong_discount_pct":       0.09,
    "M6_first_order_wrong_flag":   0.10,
}

# ─── CATALOGUE ───────────────────────────────────────────────────────────────
CATEGORIES = ["Women's Kurtas", "Men's Shirts", "Dresses", "Denim", "Ethnic Wear",
               "Activewear", "Casual Tees", "Formal Shirts", "Co-ords", "Sarees"]
BRANDS     = ["Libas", "W", "Biba", "FabIndia", "Allen Solly", "Van Heusen",
               "Zara", "H&M", "Mango", "Global Desi", "Max Fashion", "Roadster"]
COLORS     = ["Black", "White", "Navy", "Red", "Green", "Mustard", "Pink",
               "Grey", "Blue", "Ivory", "Olive", "Peach"]
SIZES      = ["XS", "S", "M", "L", "XL", "XXL"]
PLATFORMS  = ["web", "ios", "android"]
PAYMENT_METHODS = ["upi", "credit_card", "debit_card", "net_banking", "cod", "wallet"]
UTM_SOURCES     = ["instagram", "google", "facebook", "email", "direct", "influencer", None]
UTM_MEDIUMS     = {"instagram":"paid_social","google":"cpc","facebook":"paid_social",
                    "email":"email","influencer":"influencer","direct":None}
CAMPAIGN_TYPES  = ["cart_abandonment","promotion","wishlist_alert","reactivation"]

def make_sku(color, size):
    return f"SKU-{color[:3].upper()}-{size}"

def make_alt_sku(color, size):
    """Deliberately wrong SKU variant for M3."""
    alts = [f"SKU-{color.lower()}-{size.lower()}",
            f"SKU-{color[:3].upper()}-{size.upper()}-V2",
            f"PROD-{color[:3]}{size}"]
    return random.choice(alts)

PRODUCTS = []
for cat in CATEGORIES:
    for brand in random.sample(BRANDS, 4):
        color = random.choice(COLORS)
        size  = random.choice(SIZES)
        base_price = round(random.choice([299,399,499,599,699,799,999,1299,1499,1999,2499]), 2)
        on_sale    = random.random() < 0.45
        compare_at = round(base_price * random.uniform(1.2, 2.0), 2) if on_sale else None
        PRODUCTS.append({
            "product_id":       make_sku(color, size),
            "name":             f"{brand} {cat.split()[0]} {color} — {random.choice(['Regular Fit','Slim Fit','Oversized','Classic'])}",
            "brand":            brand,
            "category":         cat,
            "color":            color,
            "size":             size,
            "price":            base_price,
            "compare_at_price": compare_at,
        })

COLLECTIONS = [{"list_id": c.lower().replace("'","").replace(" ","-"), "category": c}
               for c in CATEGORIES]

# ─── USER FACTORY ─────────────────────────────────────────────────────────────
def make_users(n):
    users = []
    for _ in range(n):
        uid        = str(random.randint(1000000, 9999999))
        created    = SIM_START - timedelta(days=random.randint(0, 400))
        returning  = random.random() < 0.45
        orders_cnt = random.randint(1, 12) if returning else 0
        total_sp   = round(orders_cnt * random.uniform(500, 3000), 2)
        utm_src    = random.choice(UTM_SOURCES)
        users.append({
            "user_id":           uid,
            "anonymous_id":      str(uuid.uuid4()),
            "email":             fake.email(),
            "first_name":        fake.first_name(),
            "last_name":         fake.last_name(),
            "gender":            random.choice(["male","female","female","other"]),
            "city":              random.choice(["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Pune","Kolkata","Jaipur"]),
            "state":             fake.state(),
            "platform":          random.choice(PLATFORMS),
            "login_method":      random.choice(["email","google","phone_otp","guest"]),
            "created_at":        created.isoformat() + "Z",
            "orders_count":      orders_cnt,
            "total_spent":       total_sp,
            "accepts_marketing": random.random() < 0.6,
            "is_returning":      returning,
            "utm_source":        utm_src,
            "utm_medium":        UTM_MEDIUMS.get(utm_src),
            "utm_campaign":      f"camp-{random.randint(100,999)}" if utm_src else None,
            "app_version":       random.choice(["3.1.0","3.2.0","3.2.1","3.3.0"]),
            "_wishlist":         [],
            "_total_orders":     orders_cnt,
        })
    return users

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def ts(dt):
    return int(dt.timestamp() * 1000)

def sid(dt):
    return f"sess_{dt.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

def oid(dt):
    return f"ORD-{dt.strftime('%Y%m%d')}-{random.randint(1000,9999)}"

def cart_id():
    return f"cart_{uuid.uuid4().hex[:8]}"

def global_props(user, session_id, page_type=None, ab=None):
    p = {
        "session_id": session_id,
        "platform":   user["platform"],
        "app_version":user["app_version"],
    }
    if user["utm_source"]:  p["utm_source"]  = user["utm_source"]
    if user["utm_medium"]:  p["utm_medium"]  = user["utm_medium"]
    if user["utm_campaign"]:p["utm_campaign"]= user["utm_campaign"]
    if page_type:           p["page_type"]   = page_type
    if ab:
        p["ab_test_id"] = ab["id"]
        p["ab_variant"] = ab["variant"]
    return p

def build_event(event_type, user, session_id, dt, props, page_type=None, ab=None, mistake=None):
    ev = {
        "event_type":       event_type,
        "user_id":          user["user_id"],
        "device_id":        user["anonymous_id"],
        "time":             ts(dt),
        "event_properties": {**global_props(user, session_id, page_type, ab), **props},
        "user_properties":  {
            "gender":       user["gender"],
            "city":         user["city"],
            "platform":     user["platform"],
            "is_returning": user["is_returning"],
        },
        "insert_id": str(uuid.uuid4()),
    }
    if mistake:
        ev["_mistake"] = mistake
    return ev

# ─── MISTAKE INJECTORS ───────────────────────────────────────────────────────
def inject_M1(props):
    m = deepcopy(props)
    m["price"] = str(m["price"])
    return m, {"code":"M1","desc":"price sent as string instead of float","field":"price"}

def inject_M2_currency(props):
    m = deepcopy(props)
    m.pop("currency", None)
    return m, {"code":"M2","desc":"missing required property: currency","field":"currency"}

def inject_M2_order_id(props):
    m = deepcopy(props)
    m.pop("order_id", None)
    return m, {"code":"M2","desc":"missing required property: order_id","field":"order_id"}

def inject_M5(props, product):
    m = deepcopy(props)
    cap   = product.get("compare_at_price") or product["price"] * 1.5
    wrong = round(random.uniform(5, 95), 1)
    m["discount_pct"] = wrong
    return m, {"code":"M5","desc":f"discount_pct={wrong} but correct is {round((cap-product['price'])/cap*100,1)}","field":"discount_pct"}

# ─── SESSION SCENARIOS ───────────────────────────────────────────────────────
def session_browse_and_bounce(user, dt, ab=None):
    events = []
    sess   = sid(dt)
    t      = dt

    events.append(build_event("Page Viewed", user, sess, t,
        {"name":"Home","path":"/","page_type":"home"}, "home", ab))
    t += timedelta(seconds=random.randint(5,20))

    q = random.choice(["floral dress","linen shirt","ethnic kurta","blue jeans","casual tee"])
    events.append(build_event("Product Searched", user, sess, t,
        {"query":q,"results_count":random.randint(10,60),"has_results":True}, "search", ab))
    t += timedelta(seconds=random.randint(3,10))

    products = random.sample(PRODUCTS, random.randint(2,3))
    for prod in products:
        events.append(build_event("Page Viewed", user, sess, t,
            {"name":"Product Detail Page","path":f"/products/{prod['product_id'].lower()}","page_type":"pdp"}, "pdp", ab))
        t += timedelta(seconds=2)

        props = {
            "product_id": prod["product_id"],
            "name":       prod["name"],
            "brand":      prod["brand"],
            "category":   prod["category"],
            "price":      prod["price"],
            "currency":   "INR",
            "variant":    f"{prod['color']} / {prod['size']}",
            "color":      prod["color"],
            "size":       prod["size"],
            "in_stock":   random.random() > 0.1,
        }
        if prod["compare_at_price"]:
            props["compare_at_price"] = prod["compare_at_price"]
            correct_disc = round((prod["compare_at_price"] - prod["price"]) / prod["compare_at_price"] * 100, 1)
            if random.random() < MISTAKE_RATES["M5_wrong_discount_pct"]:
                props, mistake = inject_M5(props, prod)
                events.append(build_event("Product Viewed", user, sess, t, props, "pdp", ab, mistake))
            else:
                props["discount_pct"] = correct_disc
                events.append(build_event("Product Viewed", user, sess, t, props, "pdp", ab))
        else:
            events.append(build_event("Product Viewed", user, sess, t, props, "pdp", ab))
        t += timedelta(seconds=random.randint(20,120))

    return events

def session_add_and_abandon(user, dt, ab=None):
    events  = session_browse_and_bounce(user, dt, ab)
    sess    = events[0]["event_properties"]["session_id"]
    t       = datetime.fromtimestamp(events[-1]["time"]/1000) + timedelta(seconds=10)
    cid     = cart_id()
    prod    = random.choice(PRODUCTS)
    correct_pid = prod["product_id"]
    mistake = None

    add_props = {
        "cart_id":    cid,
        "product_id": correct_pid,
        "name":       prod["name"],
        "brand":      prod["brand"],
        "category":   prod["category"],
        "price":      prod["price"],
        "quantity":   1,
        "currency":   "INR",
        "color":      prod["color"],
        "size":       prod["size"],
        "source":     "pdp",
    }
    if random.random() < MISTAKE_RATES["M1_price_as_string"]:
        add_props, mistake = inject_M1(add_props)
    if random.random() < MISTAKE_RATES["M3_inconsistent_product_id"]:
        add_props["product_id"] = make_alt_sku(prod["color"], prod["size"])
        mistake = {"code":"M3","desc":f"product_id changed mid-funnel: PDP had '{correct_pid}', cart has '{add_props['product_id']}'","field":"product_id"}

    events.append(build_event("Product Added", user, sess, t, add_props, "pdp", ab, mistake))
    t += timedelta(seconds=random.randint(5,20))

    events.append(build_event("Cart Viewed", user, sess, t, {
        "cart_id":    cid,
        "total_items":1,
        "subtotal":   prod["price"],
        "currency":   "INR",
    }, "cart", ab))
    t += timedelta(seconds=random.randint(10,40))

    events.append(build_event("Checkout Started", user, sess, t, {
        "order_id":  oid(t),
        "cart_id":   cid,
        "revenue":   prod["price"],
        "currency":  "INR",
        "num_items": 1,
    }, "checkout", ab))

    return events

def session_happy_path(user, dt, ab=None):
    events = []
    sess   = sid(dt)
    t      = dt
    cid    = cart_id()
    order  = oid(t)
    prod   = random.choice(PRODUCTS)

    events.append(build_event("Page Viewed", user, sess, t,
        {"name":"Product Detail Page","path":f"/products/{prod['product_id'].lower()}","page_type":"pdp"}, "pdp", ab))
    t += timedelta(seconds=5)

    pdp_props = {
        "product_id": prod["product_id"], "name": prod["name"], "brand": prod["brand"],
        "category":   prod["category"],   "price": prod["price"], "currency": "INR",
        "variant":    f"{prod['color']} / {prod['size']}", "color": prod["color"],
        "size":       prod["size"], "in_stock": True,
    }
    if prod["compare_at_price"]:
        pdp_props["compare_at_price"] = prod["compare_at_price"]
        pdp_props["discount_pct"] = round((prod["compare_at_price"]-prod["price"])/prod["compare_at_price"]*100,1)
    events.append(build_event("Product Viewed", user, sess, t, pdp_props, "pdp", ab))
    t += timedelta(seconds=random.randint(30,120))

    correct_pid = prod["product_id"]
    add_props = {
        "cart_id":    cid,  "product_id": correct_pid, "name":     prod["name"],
        "brand":      prod["brand"], "category":  prod["category"], "price":    prod["price"],
        "quantity":   1,    "currency":   "INR",       "color":    prod["color"],
        "size":       prod["size"], "source": "pdp",
    }
    mistake = None
    if random.random() < MISTAKE_RATES["M1_price_as_string"]:
        add_props, mistake = inject_M1(add_props)
    if random.random() < MISTAKE_RATES["M3_inconsistent_product_id"]:
        wrong_pid = make_alt_sku(prod["color"], prod["size"])
        add_props["product_id"] = wrong_pid
        mistake = {"code":"M3","desc":f"product_id changed mid-funnel: PDP='{correct_pid}', cart='{wrong_pid}'","field":"product_id"}
    events.append(build_event("Product Added", user, sess, t, add_props, "pdp", ab, mistake))
    t += timedelta(seconds=10)

    events.append(build_event("Cart Viewed", user, sess, t, {
        "cart_id":cid,"total_items":1,"subtotal":prod["price"],"currency":"INR"}, "cart", ab))
    t += timedelta(seconds=15)

    skip_checkout_start = random.random() < MISTAKE_RATES["M4_order_without_checkout"]
    m4_mistake = None
    if not skip_checkout_start:
        events.append(build_event("Checkout Started", user, sess, t, {
            "order_id":order,"cart_id":cid,"revenue":prod["price"],"currency":"INR","num_items":1
        }, "checkout", ab))
        t += timedelta(seconds=random.randint(20,60))
    else:
        m4_mistake = {"code":"M4","desc":"Order Completed fired without Checkout Started in same session","field":"event_sequence"}

    for step_num, step_name in [(1,"address"),(2,"delivery"),(3,"payment")]:
        events.append(build_event("Checkout Step Completed", user, sess, t, {
            "order_id":    order,
            "step":        step_num,
            "step_name":   step_name,
            "payment_method": random.choice(PAYMENT_METHODS) if step_num==3 else None,
            "shipping_method":"standard" if step_num==2 else None,
        }, "checkout", ab))
        t += timedelta(seconds=random.randint(15,45))

    coupon   = random.choice(["FIRST10","SALE20","FLAT50","WELCOME15",None,None,None])
    discount = round(prod["price"] * 0.1, 2) if coupon else 0
    net_rev  = round(prod["price"] - discount, 2)
    shipping = 0 if net_rev > 999 else 49
    total    = round(net_rev + shipping, 2)

    order_props = {
        "order_id":       order,
        "revenue":        net_rev,
        "shipping":       shipping,
        "total":          total,
        "currency":       "INR",
        "payment_method": random.choice(PAYMENT_METHODS),
        "num_items":      1,
        "is_first_order": user["_total_orders"] == 0,
    }
    if coupon:
        order_props["coupon"]   = coupon
        order_props["discount"] = discount

    order_mistake = m4_mistake
    if random.random() < MISTAKE_RATES["M2_missing_required_prop"]:
        if random.random() < 0.5:
            order_props, order_mistake = inject_M2_currency(order_props)
        else:
            order_props, order_mistake = inject_M2_order_id(order_props)

    if user["is_returning"] and user["_total_orders"] > 0:
        if random.random() < MISTAKE_RATES["M6_first_order_wrong_flag"]:
            order_props["is_first_order"] = True
            order_mistake = {"code":"M6","desc":f"is_first_order=true but user has {user['_total_orders']} prior orders","field":"is_first_order"}

    events.append(build_event("Order Completed", user, sess, t, order_props, "confirmation", ab, order_mistake))
    user["_total_orders"] += 1
    return events

def session_return_customer(user, dt, ab=None):
    user["is_returning"] = True
    events = []
    sess   = sid(dt)
    t      = dt

    events.append(build_event("Page Viewed", user, sess, t,
        {"name":"Wishlist","path":"/wishlist","page_type":"wishlist"}, "wishlist", ab))
    t += timedelta(seconds=10)

    prod = random.choice(PRODUCTS)
    events.append(build_event("Wishlist Product Added", user, sess, t, {
        "product_id": prod["product_id"],
        "name":       prod["name"],
        "price":      prod["price"],
        "source":     "listing",
    }, "wishlist", ab))
    t += timedelta(seconds=5)

    events += session_happy_path(user, t + timedelta(minutes=2), ab)
    return events

def session_post_purchase(user, dt, ab=None):
    events     = []
    sess       = sid(dt)
    t          = dt
    prod       = random.choice(PRODUCTS)
    past_order = oid(dt - timedelta(days=random.randint(3,20)))

    if random.random() < 0.5:
        events.append(build_event("Product Reviewed", user, sess, t, {
            "product_id": prod["product_id"],
            "order_id":   past_order,
            "rating":     random.choices([1,2,3,4,5], weights=[3,5,10,35,47])[0],
            "has_text":   random.random() > 0.4,
            "has_media":  random.random() > 0.75,
        }, "pdp", ab))
    else:
        events.append(build_event("Return Requested", user, sess, t, {
            "order_id":     past_order,
            "product_id":   prod["product_id"],
            "reason":       random.choice(["size_issue","quality_issue","wrong_item","changed_mind","damaged","other"]),
            "return_value": prod["price"],
        }, "account", ab))
    return events

def session_notification_click(user, dt, ab=None):
    events = []
    sess   = sid(dt)
    t      = dt
    ch     = random.choice(["email","push","sms","whatsapp"])
    ctype  = random.choice(CAMPAIGN_TYPES)
    events.append(build_event("Notification Clicked", user, sess, t, {
        "campaign_id":   f"{ch.upper()}-{ctype.upper().replace('_','-')}-{random.randint(1,20):02d}",
        "channel":       ch,
        "campaign_type": ctype,
    }, "home", ab))
    t += timedelta(seconds=3)
    if ctype == "cart_abandonment":
        events += session_add_and_abandon(user, t, ab)
    else:
        events += session_browse_and_bounce(user, t, ab)
    return events

SCENARIO_WEIGHTS = {
    "browse_bounce":      30,
    "add_abandon":        20,
    "happy_path":         20,
    "return_customer":    10,
    "post_purchase":      10,
    "notification_click": 10,
}
SCENARIOS    = list(SCENARIO_WEIGHTS.keys())
WEIGHTS      = [SCENARIO_WEIGHTS[s] for s in SCENARIOS]
SCENARIO_FNS = {
    "browse_bounce":      session_browse_and_bounce,
    "add_abandon":        session_add_and_abandon,
    "happy_path":         session_happy_path,
    "return_customer":    session_return_customer,
    "post_purchase":      session_post_purchase,
    "notification_click": session_notification_click,
}

AB_TESTS = [
    {"id":"checkout-v2",     "variant":"control"},
    {"id":"checkout-v2",     "variant":"treatment_a"},
    {"id":"pdp-layout-test", "variant":"control"},
    {"id":"pdp-layout-test", "variant":"treatment_b"},
]

# ─── DATE DISTRIBUTION ───────────────────────────────────────────────────────
def session_probability(dt):
    base = 1.0
    if dt.weekday() >= 5:                  base *= 1.4
    if dt.month==1 and 20<=dt.day<=26:     base *= 1.8
    if dt.month==2 and 10<=dt.day<=14:     base *= 1.5
    if dt.month==3 and dt.day>=20:         base *= 2.0
    return base

def random_session_time(date):
    if random.random() < 0.4:
        hour = random.randint(8,11)
    elif random.random() < 0.5:
        hour = random.randint(20,23)
    else:
        hour = random.randint(6,22)
    return date.replace(hour=hour, minute=random.randint(0,59), second=random.randint(0,59))

# ─── MAIN SIMULATION ─────────────────────────────────────────────────────────
def run_simulation():
    print(f"Generating {NUM_USERS} users...")
    users       = make_users(NUM_USERS)
    all_events  = []
    mistake_log = []
    total_days  = (SIM_END - SIM_START).days + 1

    for day_offset in range(total_days):
        current_date = SIM_START + timedelta(days=day_offset)
        prob         = session_probability(current_date)
        active_users = [u for u in users if random.random() < 0.15 * prob]

        for user in active_users:
            num_sessions = random.choices([1,2,3], weights=[70,20,10])[0]
            for _ in range(num_sessions):
                dt       = random_session_time(current_date)
                scenario = random.choices(SCENARIOS, weights=WEIGHTS)[0]
                ab       = random.choice(AB_TESTS) if random.random() < 0.5 else None

                if scenario == "return_customer" and not user["is_returning"]:
                    scenario = "browse_bounce"
                if scenario == "post_purchase" and user["_total_orders"] == 0:
                    scenario = "browse_bounce"

                events = SCENARIO_FNS[scenario](user, dt, ab)

                for ev in events:
                    mistake = ev.pop("_mistake", None)
                    if mistake:
                        mistake_log.append({
                            **mistake,
                            "event_type": ev["event_type"],
                            "user_id":    ev["user_id"],
                            "insert_id":  ev["insert_id"],
                            "timestamp":  ev["time"],
                            "session_id": ev["event_properties"].get("session_id"),
                        })
                        ev["event_properties"]["_has_mistake"]   = True
                        ev["event_properties"]["_mistake_code"]  = mistake["code"]

                all_events.extend(events)

        if day_offset % 15 == 0:
            pct = round(day_offset/total_days*100)
            print(f"  {current_date.strftime('%Y-%m-%d')} — {pct}% done, {len(all_events)} events so far")

    all_events.sort(key=lambda e: e["time"])
    print(f"\nTotal events: {len(all_events)}")
    print(f"Total mistakes planted: {len(mistake_log)}")

    summary = {}
    for m in mistake_log:
        summary[m["code"]] = summary.get(m["code"], 0) + 1
    print("\nMistake breakdown:")
    for code, count in sorted(summary.items()):
        example = next(m["desc"] for m in mistake_log if m["code"]==code)
        print(f"  {code}: {count} instances — e.g. {example[:80]}")

    return all_events, mistake_log

# ─── GUARANTEED-MISTAKE SAMPLER ───────────────────────────────────────────────
def sample_with_mistakes(all_events, n=500, per_code=3):
    """
    Returns a sample of n events guaranteed to contain at least `per_code`
    instances of each mistake code M1-M6. The remainder is clean events.
    """
    faulty = [e for e in all_events if e["event_properties"].get("_has_mistake")]
    clean  = [e for e in all_events if not e["event_properties"].get("_has_mistake")]

    by_code = defaultdict(list)
    for e in faulty:
        by_code[e["event_properties"]["_mistake_code"]].append(e)

    guaranteed = []
    for code in ["M1","M2","M3","M4","M5","M6"]:
        available = by_code[code]
        pick = min(per_code, len(available))
        guaranteed += random.sample(available, pick)
        if pick < per_code:
            print(f"  Warning: only {pick} instances of {code} available (wanted {per_code})")

    remaining = n - len(guaranteed)
    if remaining < 0:
        print(f"  Warning: guaranteed mistakes ({len(guaranteed)}) exceed SEND_LIMIT ({n}). Sending all guaranteed.")
        sample = guaranteed
    else:
        sample = guaranteed + random.sample(clean, min(remaining, len(clean)))

    random.shuffle(sample)

    print(f"\nSample to send:")
    print(f"  Total  : {len(sample)}")
    print(f"  Faulty : {len(guaranteed)}  ({per_code}x each of M1–M6)")
    print(f"  Clean  : {len(sample) - len(guaranteed)}")
    return sample

# ─── AMPLITUDE SENDER ────────────────────────────────────────────────────────
def send_to_amplitude(events):
    total  = len(events)
    sent   = 0
    failed = 0

    for i in range(0, total, BATCH_SIZE):
        batch   = events[i:i+BATCH_SIZE]
        payload = {"api_key": AMPLITUDE_API_KEY, "events": batch}
        try:
            r = requests.post(AMPLITUDE_URL, json=payload, timeout=10)
            if r.status_code == 200:
                sent += len(batch)
            else:
                failed += len(batch)
                print(f"  Amplitude error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            failed += len(batch)
            print(f"  Request failed: {e}")

        if (i // BATCH_SIZE) % 50 == 0:
            print(f"  Sent {sent}/{total} events...")
        time.sleep(0.05)

    print(f"\nAmplitude: {sent} sent, {failed} failed")

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    all_events, mistake_log = run_simulation()

    print(f"\nWriting events to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_events, f, indent=2)

    print(f"Writing mistake log to {LOG_FILE}...")
    with open(LOG_FILE, "w") as f:
        json.dump({
            "total_mistakes": len(mistake_log),
            "summary": {
                "M1_price_as_string":         sum(1 for m in mistake_log if m["code"]=="M1"),
                "M2_missing_required_prop":   sum(1 for m in mistake_log if m["code"]=="M2"),
                "M3_inconsistent_product_id": sum(1 for m in mistake_log if m["code"]=="M3"),
                "M4_order_without_checkout":  sum(1 for m in mistake_log if m["code"]=="M4"),
                "M5_wrong_discount_pct":      sum(1 for m in mistake_log if m["code"]=="M5"),
                "M6_first_order_wrong_flag":  sum(1 for m in mistake_log if m["code"]=="M6"),
            },
            "mistakes": mistake_log,
        }, f, indent=2)

    if SEND_TO_AMPLITUDE:
        print(f"\nSending to Amplitude ({AMPLITUDE_URL})...")
        if SEND_LIMIT is None:
            events_to_send = all_events
            print(f"  Sending all {len(events_to_send)} events (SEND_LIMIT=None)...")
        else:
            print(f"  Building guaranteed-mistake sample (limit={SEND_LIMIT}, {MISTAKES_PER_CODE}x each M1-M6)...")
            events_to_send = sample_with_mistakes(all_events, SEND_LIMIT, MISTAKES_PER_CODE)
        send_to_amplitude(events_to_send)
    else:
        print("\nAmplitude API key not set — skipping send.")

    print("\nDone.")