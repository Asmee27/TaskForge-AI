from pathlib import Path
import pandas as pd

DATA_DIR = Path("data/raw")

EXPECTED_FILES = [
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
]


def inspect_csv(file_path: Path):
    print("\n" + "=" * 80)
    print(f"FILE: {file_path.name}")
    print("=" * 80)

    try:
        df = pd.read_csv(file_path)

        print(f"Rows       : {len(df):,}")
        print(f"Columns    : {len(df.columns)}")
        print(f"Duplicates : {df.duplicated().sum():,}")

        print("\nColumns:")
        for col in df.columns:
            missing = df[col].isna().sum()
            dtype = df[col].dtype

            print(
                f"  {col:<45}"
                f"type={str(dtype):<12}"
                f"missing={missing:,}"
            )

        print("\nSample:")
        print(df.head(2).to_string(index=False))

    except Exception as e:
        print(f"❌ Failed to read {file_path.name}")
        print(e)


def main():

    print("\nSYNAPSEOPS — DATASET AUDIT\n")

    if not DATA_DIR.exists():
        print("❌ data/raw folder not found.")
        return

    missing_files = []

    for filename in EXPECTED_FILES:
        file_path = DATA_DIR / filename

        if not file_path.exists():
            missing_files.append(filename)
        else:
            inspect_csv(file_path)

    print("\n" + "=" * 80)
    print("AUDIT SUMMARY")
    print("=" * 80)

    if missing_files:
        print("\n❌ Missing files:")
        for filename in missing_files:
            print(" -", filename)
    else:
        print("\n✅ All expected Olist datasets found.")

    print("\nDataset audit complete.")


if __name__ == "__main__":
    main()