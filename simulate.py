# simulate.py
"""
Fashion Marketplace   3-Month Event Simulation
Sends to Amplitude HTTP API v2 + dumps full JSON log.

Planted mistakes (flagged in event metadata for audit validation):
  M1   price sent as string instead of float
  M2   missing required property (currency or order_id)
  M3   inconsistent product_id mid-funnel (changes between PD and Product Added)
  M4   Order Completed fires without Checkout Started in same session
  M5   discount_pct mathematically wrong vs actual price/compare_at_price
  M6   is_first_order: true for users who already have prior orders

Simulated events (all 22 tracking plan events covered):
  Core:     Page Viewed, Product List Viewed, Product Clicked, Product Searched,
            Product Viewed, Size Chart Viewed
  Funnel:   Product Added, Cart Viewed, Checkout Started, Checkout Step Completed,
            Order Completed, Order Cancelled
  Coupon:   Coupon Applied (standalone event in coupon_purchase scenario)
  Post:     Return Requested, Product Reviewed, Wishlist Product Added/Removed
  Marketing: Promotion Viewed, Promotion Clicked, Notification Clicked
  Identity: identify (sent on login/register)
"""

import json, uuid, random, time, math, os, sys
from datetime import datetime, timedelta
from faker import Faker
import requests
from copy import deepcopy
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

fake = Faker("en_IN")
random.seed(42)

#     CONFIG                                                                   
# (Strictly use .env)
AMPLITUDE_API_KEY = os.getenv("AMPLITUDE_API_KEY")
AMPLITUDE_URL     = "https://api2.amplitude.com/2/httpapi"

if not AMPLITUDE_API_KEY:
    print("  ERROR: AMPLITUDE_API_KEY not found in .env file!")
    sys.exit(1)

SEND_TO_AMPLITUDE = True

# Dynamic window: always ends today (midnight), starts 30 days prior.
# This ensures event timestamps align with Amplitude ingestion time,
# preventing the semantic mismatch where Amplitude counts events by
# when they arrived (this month) but event `time` fields point to a
# past month — causing "Events This Month" vs dashboard filter gaps.
_today      = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
SIM_END     = _today                          # today midnight UTC (exclusive)
SIM_START   = SIM_END - timedelta(days=30)    # 30-day rolling window
NUM_USERS   = 500
BATCH_SIZE  = 200       # Optimized for stability based on user feedback
OUTPUT_FILE = "simulated_events.json"
LOG_FILE    = "mistake_log.json"

# (None = send all)
SEND_LIMIT        = None
MISTAKES_PER_CODE = 3   # guarantee at least this many of each M1-M6

# Mistake injection rates
MISTAKE_RATES = {
    "M0_unknown_event":            0.04,  # unknown event names
    "M1_price_as_string":          0.07,
    "M2_missing_required_prop":    0.05,
    "M3_inconsistent_product_id":  0.08,
    "M4_order_without_checkout":   0.06,
    "M5_wrong_discount_pct":       0.09,
    "M6_first_order_wrong_flag":   0.10,
    "M7_duplicate_event":          0.03,  # duplicate insert_id
    "M8_enum_violation":           0.05,  # invalid enum value
}

#     CATALOGUE                                                                
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
            "variant_id":       f"var_{uuid.uuid4().hex[:8]}",
            "sku":              f"SKU-{brand[:3].upper()}-{color[:2].upper()}-{size}",
            "name":             f"{brand} {cat.split()[0]} {color}   {random.choice(['Regular Fit','Slim Fit','Oversized','Classic'])}",
            "brand":            brand,
            "category":         cat,
            "color":            color,
            "size":             size,
            "price":            base_price,
            "compare_at_price": compare_at,
        })

COLLECTIONS = [{"list_id": c.lower().replace("'","").replace(" ","-"), "category": c}
               for c in CATEGORIES]

#     USER FACTORY                                                              
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

#     HELPERS                                                                  
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
        "session_id":   session_id,
        "anonymous_id": user["anonymous_id"],
        "platform":     user["platform"],
        "app_version":  user["app_version"],
        "shop_name":    "kaliper-fashion.myshopify.com",
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

#     MISTAKE INJECTORS                                                        
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

def inject_M8(props):
    """Injects an invalid enum value for 'payment_method' or 'platform'."""
    m = deepcopy(props)
    if "payment_method" in m:
        m["payment_method"] = random.choice(["bitcoin", "barter", "iou"])
        return m, {"code":"M8","desc":"invalid enum value for payment_method","field":"payment_method"}
    if "platform" in m:
        m["platform"] = "windows_mobile"
        return m, {"code":"M8","desc":"invalid enum value for platform","field":"platform"}
    return m, None

#     SESSION SCENARIOS                                                        
def session_browse_and_bounce(user, dt, ab=None):
    events = []
    sess   = sid(dt)
    t      = dt

    path = "/"
    events.append(build_event("Page Viewed", user, sess, t,
        {"name":"Home","path":path,"url":f"https://kaliper-fashion.com{path}","page_type":"home"}, "home", ab))
    t += timedelta(seconds=random.randint(5,20))

    q = random.choice(["floral dress","linen shirt","ethnic kurta","blue jeans","casual tee"])
    events.append(build_event("Product Searched", user, sess, t,
        {"query":q,"results_count":random.randint(10,60),"has_results":True}, "search", ab))
    t += timedelta(seconds=random.randint(3,10))

    products = random.sample(PRODUCTS, random.randint(2,3))
    for prod in products:
        path = f"/products/{prod['product_id'].lower()}"
        events.append(build_event("Page Viewed", user, sess, t,
            {"name":"Product Detail Page","path":path,"url":f"https://kaliper-fashion.com{path}","page_type":"pdp"}, "pdp", ab))
        t += timedelta(seconds=2)

        props = {
            "product_id": prod["product_id"],
            "variant_id": prod["variant_id"],
            "sku":        prod["sku"],
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
        "variant_id": prod["variant_id"],
        "sku":        prod["sku"],
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
        "order_id":       oid(t),
        "checkout_token": f"sh_{uuid.uuid4().hex[:12]}",
        "cart_id":        cid,
        "revenue":        prod["price"],
        "currency":       "INR",
        "num_items":      1,
    }, "checkout", ab))

    return events

def session_happy_path(user, dt, ab=None):
    events = []
    sess   = sid(dt)
    t      = dt
    cid    = cart_id()
    order  = oid(t)
    prod   = random.choice(PRODUCTS)

    path = f"/products/{prod['product_id'].lower()}"
    events.append(build_event("Page Viewed", user, sess, t,
        {"name":"Product Detail Page","path":path,"url":f"https://kaliper-fashion.com{path}","page_type":"pdp"}, "pdp", ab))
    t += timedelta(seconds=5)

    pdp_props = {
        "product_id": prod["product_id"], "variant_id": prod["variant_id"], 
        "sku": prod["sku"], "name": prod["name"], "brand": prod["brand"],
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
        "cart_id":    cid,  "product_id": correct_pid, "variant_id": prod["variant_id"],
        "sku": prod["sku"], "name": prod["name"],
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

    ctoken = f"sh_{uuid.uuid4().hex[:12]}"
    skip_checkout_start = random.random() < MISTAKE_RATES["M4_order_without_checkout"]
    m4_mistake = None
    if not skip_checkout_start:
        events.append(build_event("Checkout Started", user, sess, t, {
            "order_id":order,"checkout_token":ctoken,"cart_id":cid,"revenue":prod["price"],"currency":"INR","num_items":1
        }, "checkout", ab))
        t += timedelta(seconds=random.randint(20,60))
    else:
        m4_mistake = {"code":"M4","desc":"Order Completed fired without Checkout Started in same session","field":"event_sequence"}

    for step_num, step_name in [(1,"address"),(2,"delivery"),(3,"payment")]:
        events.append(build_event("Checkout Step Completed", user, sess, t, {
            "order_id":    order,
            "checkout_token": ctoken,
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
        "checkout_token": ctoken,
        "revenue":        net_rev,
        "shipping":       shipping,
        "tax":            round(net_rev * 0.18, 2), # 18% GST typical
        "total":          total,
        "currency":       "INR",
        "payment_method": random.choice(PAYMENT_METHODS),
        "num_items":      1,
        "is_first_order": user["_total_orders"] == 0,
        "products":       [{
            "product_id": prod["product_id"],
            "variant_id": prod["variant_id"],
            "sku":        prod["sku"],
            "name":       prod["name"],
            "price":      prod["price"],
            "quantity":   1,
            "brand":      prod["brand"],
            "category":   prod["category"]
        }]
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

    if random.random() < MISTAKE_RATES["M8_enum_violation"]:
        order_props, m8_mistake = inject_M8(order_props)
        if m8_mistake: order_mistake = m8_mistake

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

    path = "/wishlist"
    events.append(build_event("Page Viewed", user, sess, t,
        {"name":"Wishlist","path":path,"url":f"https://kaliper-fashion.com{path}","page_type":"wishlist"}, "wishlist", ab))
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


# ═══════════════════════════════════════════════════════════════════════════
# NEW SESSION FUNCTIONS — covering previously missing tracking plan events
# ═══════════════════════════════════════════════════════════════════════════

PROMOTIONS = [
    {"promotion_id": "SALE-EOS-APR26", "name": "End of Season Sale",  "creative": "hero_banner_v1", "position": "homepage_hero"},
    {"promotion_id": "SALE-EOS-APR26", "name": "End of Season Sale",  "creative": "pdp_badge_red",  "position": "pdp_badge"},
    {"promotion_id": "FLASH-WED-001",  "name": "Wednesday Flash Sale","creative": "collection_ban", "position": "collection_banner"},
    {"promotion_id": "CART-SAVE-005",  "name": "Save 10% In Cart",    "creative": "cart_ban_v2",    "position": "cart_banner"},
    {"promotion_id": "NEW-ARRIVALS-04","name": "New Arrivals Week",    "creative": "hero_banner_v3", "position": "homepage_hero"},
]

def session_product_list_and_click(user, dt, ab=None):
    """
    Simulates:  Product List Viewed → Product Clicked (1-3 products)
    Covers:     Product List Viewed, Product Clicked
    """
    events = []
    sess   = sid(dt)
    t      = dt
    coll   = random.choice(COLLECTIONS)

    events.append(build_event("Product List Viewed", user, sess, t, {
        "list_id":        coll["list_id"],
        "category":       coll["category"],
        "sort_by":        random.choice(["best_selling", "price_asc", "price_desc", "newest", "relevance"]),
        "products_shown": random.randint(12, 48),
    }, "collection", ab))
    t += timedelta(seconds=random.randint(5, 30))

    clicked_prods = [p for p in PRODUCTS if p["category"] == coll["category"]]
    if not clicked_prods:
        clicked_prods = random.sample(PRODUCTS, 3)
    clicked_prods = random.sample(clicked_prods, min(random.randint(1, 3), len(clicked_prods)))

    for pos, prod in enumerate(clicked_prods, 1):
        events.append(build_event("Product Clicked", user, sess, t, {
            "product_id": prod["product_id"],
            "name":       prod["name"],
            "brand":      prod["brand"],
            "category":   prod["category"],
            "price":      prod["price"],
            "currency":   "INR",
            "position":   pos,
            "list_id":    coll["list_id"],
        }, "collection", ab))
        t += timedelta(seconds=random.randint(3, 15))

    return events


def session_promotion(user, dt, ab=None):
    """
    Simulates:  Promotion Viewed → (55% chance) Promotion Clicked → browse
    Covers:     Promotion Viewed, Promotion Clicked
    """
    events = []
    sess   = sid(dt)
    t      = dt
    promo  = random.choice(PROMOTIONS)

    events.append(build_event("Promotion Viewed", user, sess, t, {
        "promotion_id": promo["promotion_id"],
        "name":         promo["name"],
        "creative":     promo["creative"],
        "position":     promo["position"],
    }, "home", ab))
    t += timedelta(seconds=random.randint(2, 10))

    if random.random() < 0.55:
        events.append(build_event("Promotion Clicked", user, sess, t, {
            "promotion_id": promo["promotion_id"],
            "name":         promo["name"],
            "position":     promo["position"],
        }, "home", ab))
        t += timedelta(seconds=2)
        events += session_browse_and_bounce(user, t, ab)

    return events


def session_size_chart(user, dt, ab=None):
    """
    Simulates:  Page Viewed → Product Viewed → Size Chart Viewed
    Covers:     Size Chart Viewed
    """
    events = []
    sess   = sid(dt)
    t      = dt
    prod   = random.choice(PRODUCTS)

    path = f"/products/{prod['product_id'].lower()}"
    events.append(build_event("Page Viewed", user, sess, t, {
        "name": "Product Detail Page", "path": path,
        "url": f"https://kaliper-fashion.com{path}", "page_type": "pdp",
    }, "pdp", ab))
    t += timedelta(seconds=5)

    pdp_props = {
        "product_id": prod["product_id"], "variant_id": prod["variant_id"],
        "sku": prod["sku"], "name": prod["name"], "brand": prod["brand"],
        "category": prod["category"], "price": prod["price"], "currency": "INR",
        "variant": f"{prod['color']} / {prod['size']}", "color": prod["color"],
        "size": prod["size"], "in_stock": True,
    }
    if prod["compare_at_price"]:
        pdp_props["compare_at_price"] = prod["compare_at_price"]
        pdp_props["discount_pct"] = round(
            (prod["compare_at_price"] - prod["price"]) / prod["compare_at_price"] * 100, 1)
    events.append(build_event("Product Viewed", user, sess, t, pdp_props, "pdp", ab))
    t += timedelta(seconds=random.randint(10, 40))

    events.append(build_event("Size Chart Viewed", user, sess, t, {
        "product_id": prod["product_id"],
        "category":   prod["category"],
    }, "pdp", ab))
    return events


def session_coupon_purchase(user, dt, ab=None):
    """
    Full purchase flow with Coupon Applied fired as a standalone event.
    Covers:     Coupon Applied (plus Product Added, Checkout Started, Order Completed)
    """
    events      = []
    sess        = sid(dt)
    t           = dt
    cid         = cart_id()
    order       = oid(t)
    prod        = random.choice(PRODUCTS)
    ctoken      = f"sh_{uuid.uuid4().hex[:12]}"
    coupon_code = random.choice(["FIRST10", "SALE20", "FLAT50", "WELCOME15"])
    discount_amt = round(prod["price"] * 0.1, 2)
    net_rev      = round(prod["price"] - discount_amt, 2)
    shipping     = 0 if net_rev > 999 else 49
    total        = round(net_rev + shipping, 2)

    path = f"/products/{prod['product_id'].lower()}"
    events.append(build_event("Page Viewed", user, sess, t, {
        "name": "Product Detail Page", "path": path,
        "url": f"https://kaliper-fashion.com{path}", "page_type": "pdp",
    }, "pdp", ab))
    t += timedelta(seconds=5)

    events.append(build_event("Product Added", user, sess, t, {
        "cart_id": cid, "product_id": prod["product_id"], "variant_id": prod["variant_id"],
        "sku": prod["sku"], "name": prod["name"], "brand": prod["brand"],
        "category": prod["category"], "price": prod["price"],
        "quantity": 1, "currency": "INR",
        "color": prod["color"], "size": prod["size"], "source": "pdp",
    }, "pdp", ab))
    t += timedelta(seconds=10)

    events.append(build_event("Cart Viewed", user, sess, t, {
        "cart_id": cid, "total_items": 1, "subtotal": prod["price"], "currency": "INR",
    }, "cart", ab))
    t += timedelta(seconds=15)

    events.append(build_event("Checkout Started", user, sess, t, {
        "order_id": order, "checkout_token": ctoken, "cart_id": cid,
        "revenue": prod["price"], "currency": "INR", "num_items": 1,
    }, "checkout", ab))
    t += timedelta(seconds=20)

    # Standalone Coupon Applied event
    events.append(build_event("Coupon Applied", user, sess, t, {
        "order_id": order,
        "coupon":   coupon_code,
        "discount": discount_amt,
        "is_valid": True,
    }, "checkout", ab))
    t += timedelta(seconds=5)

    for step_num, step_name in [(1, "address"), (2, "delivery"), (3, "payment")]:
        events.append(build_event("Checkout Step Completed", user, sess, t, {
            "order_id": order, "checkout_token": ctoken,
            "step": step_num, "step_name": step_name,
            "payment_method": random.choice(PAYMENT_METHODS) if step_num == 3 else None,
            "shipping_method": "standard" if step_num == 2 else None,
        }, "checkout", ab))
        t += timedelta(seconds=random.randint(15, 45))

    events.append(build_event("Order Completed", user, sess, t, {
        "order_id": order, "checkout_token": ctoken,
        "revenue": net_rev, "shipping": shipping,
        "tax": round(net_rev * 0.18, 2),
        "total": total, "currency": "INR",
        "payment_method": random.choice(PAYMENT_METHODS),
        "num_items": 1, "is_first_order": user["_total_orders"] == 0,
        "coupon": coupon_code, "discount": discount_amt,
        "products": [{
            "product_id": prod["product_id"], "variant_id": prod["variant_id"],
            "sku": prod["sku"], "name": prod["name"],
            "price": prod["price"], "quantity": 1,
            "brand": prod["brand"], "category": prod["category"],
        }],
    }, "confirmation", ab))
    user["_total_orders"] += 1
    return events


def session_order_cancelled(user, dt, ab=None):
    """
    Simulates Order Cancelled for a recent past order.
    Covers:     Order Cancelled
    Falls back to browse_bounce for users with no order history.
    """
    if user["_total_orders"] == 0 or not user["is_returning"]:
        return session_browse_and_bounce(user, dt, ab)

    events = []
    sess   = sid(dt)
    t      = dt
    past   = oid(dt - timedelta(days=random.randint(1, 5)))
    prod   = random.choice(PRODUCTS)

    events.append(build_event("Order Cancelled", user, sess, t, {
        "order_id": past,
        "reason":   random.choice(["changed_mind", "wrong_size", "found_cheaper",
                                   "delivery_too_long", "other"]),
        "total":    prod["price"],
    }, "account", ab))
    return events


def session_wishlist_remove(user, dt, ab=None):
    """
    Simulates Wishlist Product Added followed by Wishlist Product Removed.
    Covers:     Wishlist Product Removed
    """
    events = []
    sess   = sid(dt)
    t      = dt
    prod   = random.choice(PRODUCTS)

    events.append(build_event("Wishlist Product Added", user, sess, t, {
        "product_id": prod["product_id"],
        "name":       prod["name"],
        "price":      prod["price"],
        "source":     "listing",
    }, "wishlist", ab))
    t += timedelta(seconds=random.randint(30, 300))

    events.append(build_event("Wishlist Product Removed", user, sess, t, {
        "product_id": prod["product_id"],
    }, "wishlist", ab))
    return events


SCENARIO_WEIGHTS = {
    "browse_bounce":         26,
    "add_abandon":           17,
    "happy_path":            16,
    "return_customer":        8,
    "post_purchase":          8,
    "notification_click":     8,
    "product_list_click":     5,   # Product List Viewed + Product Clicked
    "promotion":              5,   # Promotion Viewed/Clicked
    "coupon_purchase":        4,   # Coupon Applied + full funnel
    "order_cancelled":        2,   # Order Cancelled
    "size_chart":             3,   # Size Chart Viewed
    "wishlist_remove":        2,   # Wishlist Product Removed
}
SCENARIOS    = list(SCENARIO_WEIGHTS.keys())
WEIGHTS      = [SCENARIO_WEIGHTS[s] for s in SCENARIOS]
SCENARIO_FNS = {
    "browse_bounce":         session_browse_and_bounce,
    "add_abandon":           session_add_and_abandon,
    "happy_path":            session_happy_path,
    "return_customer":       session_return_customer,
    "post_purchase":         session_post_purchase,
    "notification_click":    session_notification_click,
    "product_list_click":    session_product_list_and_click,
    "promotion":             session_promotion,
    "coupon_purchase":       session_coupon_purchase,
    "order_cancelled":       session_order_cancelled,
    "size_chart":            session_size_chart,
    "wishlist_remove":       session_wishlist_remove,
}

AB_TESTS = [
    {"id":"checkout-v2",     "variant":"control"},
    {"id":"checkout-v2",     "variant":"treatment_a"},
    {"id":"pdp-layout-test", "variant":"control"},
    {"id":"pdp-layout-test", "variant":"treatment_b"},
]

#     DATE DISTRIBUTION                                                        
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

#     MAIN SIMULATION                                                          
def run_simulation():
    print(f"Generating {NUM_USERS} users...")
    users       = make_users(NUM_USERS)
    all_events  = []
    mistake_log = []
    # Use .days without +1: SIM_END is exclusive (today midnight).
    # Adding 1 previously caused events to be generated on the boundary
    # date (SIM_END itself), contributing to the ~400 event gap when
    # filtering by [SIM_START, SIM_END) in the dashboard.
    total_days  = (SIM_END - SIM_START).days

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
                if scenario == "order_cancelled" and user["_total_orders"] == 0:
                    scenario = "browse_bounce"

                events = SCENARIO_FNS[scenario](user, dt, ab)

                # M0 Injection
                if random.random() < MISTAKE_RATES.get("M0_unknown_event", 0):
                    m0_ev = random.choice(events)
                    m0_ev["event_type"] = random.choice(["Button Clicked", "scrolled_depth", "debug_pixel"])
                    m0_ev["_mistake"] = {"code":"M0", "desc": f"Unknown event name: {m0_ev['event_type']}", "field":"event_type"}

                # M7 Injection
                if random.random() < MISTAKE_RATES.get("M7_duplicate_event", 0):
                    m7_ev = deepcopy(random.choice(events))
                    m7_ev["_mistake"] = {"code":"M7", "desc": "Duplicate insert_id (event re-sent)", "field":"insert_id"}
                    events.append(m7_ev)

                # No more hidden flags! Pure, blind events.
                all_events.extend(events)

        if day_offset % 15 == 0:
            pct = round(day_offset/total_days*100)
            print(f"  {current_date.strftime('%Y-%m-%d')}   {pct}% done, {len(all_events)} events so far")
            sys.stdout.flush()

    # Collect mistakes from the events themselves for the ground truth log
    mistake_log = [e["_mistake"] for e in all_events if "_mistake" in e]
    print(f"\nTotal events: {len(all_events)}")
    print(f"Total mistakes planted: {len(mistake_log)}")

    summary = {}
    mistake_log = [e["_mistake"] for e in all_events if "_mistake" in e]
    
    for m in mistake_log:
        summary[m["code"]] = summary.get(m["code"], 0) + 1
    print("\nMistake breakdown:")
    for code, count in sorted(summary.items()):
        example = next(m["desc"] for m in mistake_log if m["code"]==code)
        print(f"  {code}: {count} instances   e.g. {example[:80]}")

    return all_events, mistake_log

#     GUARANTEED-MISTAKE SAMPLER                                                
def sample_random_sessions(all_events, n=500):
    """
    Returns a random sample of n events, ensuring session integrity 
    (no beheading). This is a production-ready sampling strategy.
    """
    session_map = {}
    for ev in all_events:
        sid = ev["event_properties"].get("session_id", "none")
        session_map.setdefault(sid, []).append(ev)
    
    sids = list(session_map.keys())
    random.shuffle(sids)
    
    sample = []
    current_count = 0
    for sid in sids:
        sess = session_map[sid]
        if current_count + len(sess) <= n:
            sample.extend(sess)
            current_count += len(sess)
        if current_count >= n:
            break
            
    print(f"\nRandom Sample to send:")
    print(f"  Total events : {len(sample)}")
    print(f"  Total sessions: {len(set(ev['event_properties'].get('session_id') for ev in sample))}")
    return sample

#     AMPLITUDE SENDER                                                         
def send_to_amplitude(events):
    total  = len(events)
    sent   = 0
    failed = 0

    for i in range(0, total, BATCH_SIZE):
        batch   = events[i:i+BATCH_SIZE]
        clean_batch = [{k: v for k, v in e.items() if k != '_mistake'} for e in batch]
        payload = {"api_key": AMPLITUDE_API_KEY, "events": clean_batch}
        
        success = False
        retries = 0
        max_retries = 5
        wait = 2

        while not success and retries < max_retries:
            try:
                r = requests.post(AMPLITUDE_URL, json=payload, timeout=20)
                if r.status_code == 200:
                    sent += len(batch)
                    success = True
                elif r.status_code == 429:
                    print(f"  Rate limited (429). Sleeping {wait}s...")
                    time.sleep(wait)
                    retries += 1
                    wait *= 2
                else:
                    print(f"  Amplitude error {r.status_code}: {r.text[:200]}")
                    failed += len(batch)
                    success = True # Don't retry logic errors
            except Exception as e:
                print(f"  Connection error: {e}. Retrying ({retries+1}/{max_retries}) in {wait}s...")
                time.sleep(wait)
                retries += 1
                wait *= 2
        
        if not success:
            failed += len(batch)

        if (i // BATCH_SIZE) % 5 == 0:
            print(f"  Progress: {sent}/{total} events ({round(sent/total*100)}%)...")
            sys.stdout.flush()

        if (i // BATCH_SIZE) % 10 == 0:
            print(f"  Sent {sent}/{total} events ({round(sent/total*100)}%)...")
            sys.stdout.flush()
        time.sleep(0.01)

    print(f"\nAmplitude: {sent} sent, {failed} failed")

#     ENTRY POINT                                                              
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
                "M0_unknown_event":           sum(1 for m in mistake_log if m["code"]=="M0"),
                "M7_duplicate_event":         sum(1 for m in mistake_log if m["code"]=="M7"),
                "M8_enum_violation":          sum(1 for m in mistake_log if m["code"]=="M8"),
            },
            "mistakes": mistake_log,
        }, f, indent=2)

    if SEND_TO_AMPLITUDE:
        print(f"\nSending to Amplitude ({AMPLITUDE_URL})...")
        if SEND_LIMIT is None:
            events_to_send = all_events
            print(f"  Sending all {len(events_to_send)} events (SEND_LIMIT=None)...")
        else:
            print(f"  Building randomized representative sample (limit={SEND_LIMIT})...")
            events_to_send = sample_random_sessions(all_events, SEND_LIMIT)
        send_to_amplitude(events_to_send)
    else:
        print("\nAmplitude API key not set   skipping send.")

    print("\nDone.")
