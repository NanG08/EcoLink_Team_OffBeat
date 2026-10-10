#!/usr/bin/env python
"""Load demo data: 8 recyclers, a sample user and the rate table. Safe to re-run (upserts).

    python scripts/seed.py            # RECYCLER_PHONE and DEMO_USER_ID are read from .env
    python scripts/seed.py --recycler-phone +91XXXXXXXXXX --user-id whatsapp:+91YYYYYYYYYY

--recycler-phone : a real phone that will receive the voice offers (all 8 recyclers share it,
                   so any match rings the demo phone). With a Twilio trial account it must be
                   a verified caller ID.
--user-id        : the demo household's WhatsApp id (it must have joined the Twilio sandbox).
Table names are <prefix>-users etc. (default prefix "EcoLink"); AWS credentials/region come
from the usual boto3 sources (AWS_PROFILE, AWS_REGION, ...).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import demo_data  # noqa: E402
import envfile  # noqa: E402
from ecolink.core.repository import to_dynamo  # noqa: E402
from ecolink.schema import DEFAULT_PREFIX, table_name  # noqa: E402

PHONE_RE = re.compile(r"^\+\d{8,15}$")
USER_ID_RE = re.compile(r"^whatsapp:\+\d{8,15}$")


def seed_all(
    ddb: Any, *, prefix: str, recycler_phone: str, user_id: str,
    user_lat: float = demo_data.BASE_LAT, user_lng: float = demo_data.BASE_LNG,
) -> dict[str, int]:
    """Upsert everything through a boto3 DynamoDB *resource*. Returns row counts per table."""
    plan = {
        "recyclers": demo_data.recyclers(recycler_phone),
        "users": [demo_data.sample_user(user_id, user_lat, user_lng)],
        "rate_table": demo_data.rate_rows(),
    }
    for key, rows in plan.items():
        with ddb.Table(table_name(key, prefix)).batch_writer() as batch:
            for row in rows:
                batch.put_item(Item=to_dynamo(row))
    return {key: len(rows) for key, rows in plan.items()}


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="table name prefix (default: %(default)s)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1")
    parser.add_argument("--recycler-phone", default=os.environ.get("RECYCLER_PHONE"),
                        help="E.164 phone that receives voice offers (default: RECYCLER_PHONE in .env)")
    parser.add_argument("--user-id", default=os.environ.get("DEMO_USER_ID"),
                        help="demo household id, e.g. whatsapp:+919812345678 (default: DEMO_USER_ID in .env)")
    parser.add_argument("--user-lat", type=float, default=demo_data.BASE_LAT)
    parser.add_argument("--user-lng", type=float, default=demo_data.BASE_LNG)


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.recycler_phone or not args.user_id:
        parser.error("--recycler-phone and --user-id are required (or set RECYCLER_PHONE / DEMO_USER_ID in .env)")
    if not PHONE_RE.match(args.recycler_phone):
        parser.error("--recycler-phone must be E.164, e.g. +919812345678")
    if not USER_ID_RE.match(args.user_id):
        parser.error("--user-id must look like whatsapp:+919812345678")


def main() -> None:
    envfile.load_env()
    parser =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    args = parser.parse_args()
    validate_args(args, parser)

    import boto3

    ddb = boto3.resource("dynamodb", region_name=args.region)
    counts = seed_all(ddb, prefix=args.prefix, recycler_phone=args.recycler_phone,
                      user_id=args.user_id, user_lat=args.user_lat, user_lng=args.user_lng)
    for key, n in counts.items():
        print(f"seeded {n:>2} rows into {table_name(key, args.prefix)}")


if __name__ == "__main__":
    main()
