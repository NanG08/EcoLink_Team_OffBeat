"""
Load ml/pricing/rate_table.json into the backend's DynamoDB rate_table, so the
deployed GET /price and POST /ewaste/quote serve these numbers (with the real
'updated' date of the rates, not the date the table was seeded).

    python ml/pricing/seed_rates.py --dry-run          # validate + print rows, no AWS calls
    python ml/pricing/seed_rates.py --region ap-south-1

Run it AFTER scripts/seed.py: seed.py writes its own demo rate rows to the same
keys, and this script overwrites them (upsert).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pricing.rates import RATE_TABLE_PATH, load  # noqa: E402  (also puts backend/ on sys.path)
from ecolink.schema import DEFAULT_PREFIX, table_name  # noqa: E402


def _to_dynamo(value: Any) -> Any:
    """boto3's resource layer rejects float; convert recursively to Decimal."""
    from decimal import Decimal

    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def seed_rates(ddb: Any, *, prefix: str = DEFAULT_PREFIX, path: str | Path = RATE_TABLE_PATH) -> int:
    """Upsert every row through a boto3 DynamoDB *resource*. Returns the row count."""
    rows = load(path).dynamo_rows()
    with ddb.Table(table_name("rate_table", prefix)).batch_writer() as batch:
        for row in rows:
            batch.put_item(Item=_to_dynamo(row))
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rates", default=str(RATE_TABLE_PATH), help="path to rate_table.json")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="table name prefix (default: %(default)s)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the rows without writing")
    args = parser.parse_args()

    table = load(args.rates)
    if table.is_placeholder:
        print("WARNING: rate_table.json is marked is_placeholder=true. These are NOT market rates.", file=sys.stderr)
    if args.dry_run:
        for row in table.dynamo_rows():
            print(json.dumps({k: v for k, v in row.items() if k != "source"}))
        print(f"{len(table.dynamo_rows())} rows valid (dry run, nothing written)")
        return

    import boto3

    n = seed_rates(boto3.resource("dynamodb", region_name=args.region), prefix=args.prefix, path=args.rates)
    print(f"seeded {n} rows into {table_name('rate_table', args.prefix)} (rates updated {table.updated})")


if __name__ == "__main__":
    main()
