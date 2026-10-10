"""DynamoDB table definitions: the single source of truth.

Consumed by the CDK stack (infra/ecolink_stack.py), scripts/reset.py and the tests, so the
schema the code runs against in tests is the one that gets deployed. Table names are
"<prefix>-<key>" with prefix "EcoLink" by default (matches the agent's
"EcoLink-conversation-state" default).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_PREFIX = "EcoLink"


@dataclass(frozen=True)
class Gsi:
    name: str
    pk: str
    sk: str


@dataclass(frozen=True)
class TableSpec:
    pk: str
    sk: str | None = None
    gsis: tuple[Gsi, ...] = field(default_factory=tuple)
    ttl: str | None = None
    env_var: str = ""  # Lambda environment variable that carries the table name


TABLES: dict[str, TableSpec] = {
    "users": TableSpec(pk="user_id", env_var="USERS_TABLE"),
    "recyclers": TableSpec(pk="recycler_id", env_var="RECYCLERS_TABLE"),
    "pickups": TableSpec(
        pk="pickup_id",
        gsis=(Gsi("by_status", "status", "created_at"), Gsi("by_user", "user_id", "created_at")),
        env_var="PICKUPS_TABLE",
    ),
    "rate_table": TableSpec(pk="kind", sk="item", env_var="RATE_TABLE"),
    # Owned by the agent (agent/state.py): PK user_id, TTL attribute `ttl`.
    "conversation_state": TableSpec(pk="user_id", ttl="ttl", env_var="DYNAMODB_STATE_TABLE"),
}


def table_name(key: str, prefix: str = DEFAULT_PREFIX) -> str:
    return f"{prefix}-{key.replace('_', '-')}"


def boto_create_params(key: str, prefix: str = DEFAULT_PREFIX) -> dict:
    """Keyword arguments for boto3 `create_table` (used by tests; CDK builds its own)."""
    spec = TABLES[key]
    attrs = {spec.pk, *([spec.sk] if spec.sk else [])}
    key_schema = [{"AttributeName": spec.pk, "KeyType": "HASH"}]
    if spec.sk:
        key_schema.append({"AttributeName": spec.sk, "KeyType": "RANGE"})
    params: dict = {
        "TableName": table_name(key, prefix),
        "KeySchema": key_schema,
        "BillingMode": "PAY_PER_REQUEST",
    }
    if spec.gsis:
        params["GlobalSecondaryIndexes"] = [
            {
                "IndexName": g.name,
                "KeySchema": [
                    {"AttributeName": g.pk, "KeyType": "HASH"},
                    {"AttributeName": g.sk, "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
            for g in spec.gsis
        ]
        for g in spec.gsis:
            attrs |= {g.pk, g.sk}
    params["AttributeDefinitions"] = [{"AttributeName": a, "AttributeType": "S"} for a in sorted(attrs)]
    return params
