#!/usr/bin/env python
"""Restore the demo state: wipe users, recyclers, pickups, conversation state and rates,
then re-seed (see seed.py). Optionally empty the image bucket.

    python scripts/reset.py --yes     # RECYCLER_PHONE and DEMO_USER_ID are read from .env
    python scripts/reset.py --yes --recycler-phone +91XXXXXXXXXX --user-id whatsapp:+91YYYYYYYYYY

DESTRUCTIVE: every item in the five tables is deleted. --yes is required.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import envfile  # noqa: E402
import seed  # noqa: E402
from ecolink.schema import TABLES, table_name  # noqa: E402


def wipe_table(ddb: Any, key: str, prefix: str) -> int:
    spec = TABLES[key]
    table = ddb.Table(table_name(key, prefix))
    key_names = [spec.pk] + ([spec.sk] if spec.sk else [])
    deleted = 0
    kwargs: dict[str, Any] = {"ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(key_names))),
                              "ExpressionAttributeNames": {f"#k{i}": n for i, n in enumerate(key_names)}}
    while True:
        page = table.scan(**kwargs)
        with table.batch_writer() as batch:
            for item in page["Items"]:
                batch.delete_item(Key={n: item[n] for n in key_names})
                deleted += 1
        if "LastEvaluatedKey" not in page:
            return deleted
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def empty_bucket(s3: Any, bucket: str) -> int:
    deleted = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            deleted += len(objects)
    return deleted


def reset_all(ddb: Any, *, prefix: str, **seed_kwargs: Any) -> dict[str, int]:
    for key in TABLES:
        wipe_table(ddb, key, prefix)
    return seed.seed_all(ddb, prefix=prefix, **seed_kwargs)


def main() -> None:
    envfile.load_env()
    parser =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    seed.add_common_args(parser)
    parser.add_argument("--yes", action="store_true", help="confirm that all data in the tables may be deleted")
    parser.add_argument("--purge-images", metavar="BUCKET", help="also delete every object in this S3 bucket")
    args = parser.parse_args()
    seed.validate_args(args, parser)

    targets = ", ".join(table_name(k, args.prefix) for k in TABLES)
    if not args.yes:
        parser.error(f"refusing to delete all data in [{targets}] in {args.region} without --yes")

    import boto3

    ddb = boto3.resource("dynamodb", region_name=args.region)
    counts = reset_all(ddb, prefix=args.prefix, recycler_phone=args.recycler_phone, user_id=args.user_id,
                       user_lat=args.user_lat, user_lng=args.user_lng)
    for key, n in counts.items():
        print(f"reset {table_name(key, args.prefix)}: seeded {n} rows")
    if args.purge_images:
        n = empty_bucket(boto3.client("s3", region_name=args.region), args.purge_images)
        print(f"deleted {n} objects from s3://{args.purge_images}")
    print("demo state restored")


if __name__ == "__main__":
    main()
