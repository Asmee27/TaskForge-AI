from pathlib import Path
import sqlite3
import pandas as pd

RAW_DIR = Path("data/raw")
DB_DIR = Path("data/processed")
DB_PATH = DB_DIR / "novamart.db"

DB_DIR.mkdir(parents=True, exist_ok=True)

FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def load_csv(name):
    path = RAW_DIR / FILES[name]

    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")

    print(f"Loading {name}...")
    return pd.read_csv(path)


def clean_dataframes():
    customers = load_csv("customers")
    orders = load_csv("orders")
    order_items = load_csv("order_items")
    payments = load_csv("payments")
    reviews = load_csv("reviews")
    products = load_csv("products")
    sellers = load_csv("sellers")
    geolocation = load_csv("geolocation")
    translation = load_csv("category_translation")

    # -------------------------
    # CUSTOMERS
    # -------------------------
    customers = customers.drop_duplicates(subset=["customer_id"])

    # -------------------------
    # ORDERS
    # -------------------------
    date_columns = [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ]

    for col in date_columns:
        orders[col] = pd.to_datetime(orders[col], errors="coerce")

    orders = orders.drop_duplicates(subset=["order_id"])

    # -------------------------
    # ORDER ITEMS
    # -------------------------
    order_items["shipping_limit_date"] = pd.to_datetime(
        order_items["shipping_limit_date"],
        errors="coerce",
    )

    order_items["price"] = pd.to_numeric(
        order_items["price"],
        errors="coerce",
    )

    order_items["freight_value"] = pd.to_numeric(
        order_items["freight_value"],
        errors="coerce",
    )

    # -------------------------
    # PAYMENTS
    # -------------------------
    payments["payment_value"] = pd.to_numeric(
        payments["payment_value"],
        errors="coerce",
    )

    payments["payment_installments"] = pd.to_numeric(
        payments["payment_installments"],
        errors="coerce",
    )

    # -------------------------
    # REVIEWS
    # -------------------------
    reviews["review_creation_date"] = pd.to_datetime(
        reviews["review_creation_date"],
        errors="coerce",
    )

    reviews["review_answer_timestamp"] = pd.to_datetime(
        reviews["review_answer_timestamp"],
        errors="coerce",
    )

    # Keep review_id uniqueness
    reviews = reviews.drop_duplicates(subset=["review_id"])

    # -------------------------
    # PRODUCTS
    # -------------------------
    products = products.merge(
        translation,
        how="left",
        on="product_category_name",
    )

    products = products.drop_duplicates(subset=["product_id"])

    # -------------------------
    # SELLERS
    # -------------------------
    sellers = sellers.drop_duplicates(subset=["seller_id"])

    # -------------------------
    # GEOLOCATION
    # Olist geolocation contains many repeated ZIP prefix rows.
    # We aggregate instead of storing unnecessary duplicates.
    # -------------------------
    geolocation_clean = (
        geolocation
        .groupby("geolocation_zip_code_prefix", as_index=False)
        .agg({
            "geolocation_lat": "mean",
            "geolocation_lng": "mean",
            "geolocation_city": "first",
            "geolocation_state": "first",
        })
    )

    return {
        "customers": customers,
        "orders": orders,
        "order_items": order_items,
        "payments": payments,
        "reviews": reviews,
        "products": products,
        "sellers": sellers,
        "geolocation": geolocation_clean,
    }


def create_inventory_table(conn, products):
    """
    Synthetic operational inventory table.

    Olist does not contain stock levels, so we create realistic demo
    inventory values for the hackathon.
    """

    inventory = products[
        ["product_id"]
    ].copy()

    # deterministic synthetic values
    inventory["stock_quantity"] = (
        inventory.index % 120
    ) + 5

    inventory["reorder_level"] = (
        inventory.index % 25
    ) + 10

    inventory["warehouse_id"] = (
        inventory.index % 5
    ) + 1

    inventory["supplier_lead_days"] = (
        inventory.index % 14
    ) + 2

    inventory.to_sql(
        "inventory",
        conn,
        if_exists="replace",
        index=False,
    )


def create_business_targets(conn):
    targets = pd.DataFrame([
        {
            "metric_name": "monthly_revenue_growth",
            "target_value": 5.0,
            "unit": "percent",
        },
        {
            "metric_name": "minimum_avg_review_score",
            "target_value": 4.0,
            "unit": "score",
        },
        {
            "metric_name": "maximum_late_delivery_rate",
            "target_value": 8.0,
            "unit": "percent",
        },
    ])

    targets.to_sql(
        "business_targets",
        conn,
        if_exists="replace",
        index=False,
    )


def create_indexes(conn):
    cursor = conn.cursor()

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status)",
        "CREATE INDEX IF NOT EXISTS idx_orders_purchase_date ON orders(order_purchase_timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_product ON order_items(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_seller ON order_items(seller_id)",
        "CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_reviews_order ON reviews(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_inventory_product ON inventory(product_id)",
    ]

    for sql in indexes:
        cursor.execute(sql)

    conn.commit()


def validate_database(conn):
    print("\nDATABASE VALIDATION")
    print("=" * 60)

    tables = [
        "customers",
        "orders",
        "order_items",
        "payments",
        "reviews",
        "products",
        "sellers",
        "geolocation",
        "inventory",
        "business_targets",
    ]

    cursor = conn.cursor()

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]

        print(f"{table:<20} {count:,} rows")

    # Check for broken references
    checks = {
        "items_without_order": """
            SELECT COUNT(*)
            FROM order_items oi
            LEFT JOIN orders o
                ON oi.order_id = o.order_id
            WHERE o.order_id IS NULL
        """,

        "payments_without_order": """
            SELECT COUNT(*)
            FROM payments p
            LEFT JOIN orders o
                ON p.order_id = o.order_id
            WHERE o.order_id IS NULL
        """,

        "reviews_without_order": """
            SELECT COUNT(*)
            FROM reviews r
            LEFT JOIN orders o
                ON r.order_id = o.order_id
            WHERE o.order_id IS NULL
        """,
    }

    print("\nRelationship checks:")

    for name, query in checks.items():
        cursor.execute(query)
        value = cursor.fetchone()[0]
        print(f"{name:<25} {value}")


def build_database():
    print("\nSynapseOps — Building NovaMart Database\n")

    data = clean_dataframes()

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)

    try:
        for table_name, df in data.items():

            print(
                f"Writing {table_name:<15}"
                f"{len(df):>10,} rows"
            )

            df.to_sql(
                table_name,
                conn,
                if_exists="replace",
                index=False,
            )

        create_inventory_table(
            conn,
            data["products"],
        )

        create_business_targets(conn)

        create_indexes(conn)

        validate_database(conn)

        print("\n✅ NovaMart database created successfully.")
        print(f"Database: {DB_PATH}")

    finally:
        conn.close()


if __name__ == "__main__":
    build_database()