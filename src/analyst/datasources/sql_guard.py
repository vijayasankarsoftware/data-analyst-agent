"""Application-level SQL validation using a real parser (sqlglot), not regexes.

Rules (all must pass):
1. exactly one statement, and it must be a query (SELECT / UNION / ...),
2. no write/DDL/session nodes anywhere in the tree (incl. data-modifying CTEs, SELECT INTO, FOR UPDATE),
3. no dangerous functions (pg_sleep, set_config, dblink, read_csv, query_to_xml, ...),
4. every table must be a plain identifier in the allowlist (no table functions, no file paths,
   no other catalogs, no system schemas unless allowlisted),
5. a LIMIT no larger than max_rows is enforced.

Also reports which PII columns the query touches so the caller can require human approval.
"""

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_FORBIDDEN_NODE_NAMES = [
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    "Command",
    "Set",
    "Into",
    "Lock",
    "Copy",
    "Grant",
    "Pragma",
    "Use",
    "Transaction",
    "Commit",
    "Rollback",
    "LoadData",
    "Attach",
    "Detach",
    "Install",
]
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    t for n in _FORBIDDEN_NODE_NAMES if isinstance(t := getattr(exp, n, None), type)
)

_FORBIDDEN_FUNCS = re.compile(
    r"^(pg_|lo_|dblink|set_config|current_setting|query_to|table_to|schema_to|database_to"
    r"|cursor_to|read_|parquet_|sniff_csv|glob$|getenv|query$|query_table|http|txid_"
    r"|nextval|setval|currval|copy|system|shell|load_extension)",
    re.IGNORECASE,
)


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    errors: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    pii_columns: list[str] = field(default_factory=list)


def _func_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name)
    return node.sql_name()


def validate_sql(
    sql: str,
    *,
    dialect: str,
    allowed_tables: set[str],
    max_rows: int,
    pii_columns: dict[str, set[str]] | None = None,
    default_schema: str = "public",
) -> GuardResult:
    """`allowed_tables` holds lowercase qualified names, e.g. {'public.orders'}."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return GuardResult(ok=False, errors=["empty SQL"])
    if len(sql) > 20_000:
        return GuardResult(ok=False, errors=["SQL is too long"])

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        return GuardResult(ok=False, errors=[f"could not parse SQL: {str(e).splitlines()[0]}"])
    if len(statements) != 1:
        return GuardResult(ok=False, errors=["exactly one SQL statement is allowed"])
    root = statements[0]

    errors: list[str] = []
    if not isinstance(root, exp.Query):
        errors.append(f"only SELECT queries are allowed (got {root.key.upper()})")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            errors.append(f"forbidden operation: {node.key.upper()}")
        elif isinstance(node, exp.Func) and _FORBIDDEN_FUNCS.match(_func_name(node)):
            errors.append(f"forbidden function: {_func_name(node).lower()}")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    by_bare: dict[str, list[str]] = {}
    for q in allowed_tables:
        by_bare.setdefault(q.split(".")[-1], []).append(q)

    tables: list[str] = []
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            errors.append("table functions / file paths are not allowed in FROM")
            continue
        name, db, catalog = table.name.lower(), table.db.lower(), table.catalog.lower()
        if catalog:
            errors.append(f"cross-database reference not allowed: {table.sql(dialect=dialect)}")
            continue
        if not db and name in cte_names:
            continue
        if db:
            qualified = f"{db}.{name}"
        elif f"{default_schema}.{name}" in allowed_tables:
            qualified = f"{default_schema}.{name}"
        elif len(by_bare.get(name, [])) == 1:
            qualified = by_bare[name][0]
        else:
            qualified = name
        if qualified not in allowed_tables:
            errors.append(f"unknown or not-allowed table: {qualified}")
        elif qualified not in tables:
            tables.append(qualified)

    if errors:
        return GuardResult(ok=False, errors=sorted(set(errors)), tables=tables)

    # PII: explicit column references, or SELECT * over a table that has PII columns.
    pii_hits: list[str] = []
    if pii_columns:
        referenced = {c.name.lower() for c in root.find_all(exp.Column)}
        # Only a projected `*` / `t.*` counts; count(*) does not expose column values.
        star = any(
            isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
            for sel in root.find_all(exp.Select)
            for e in sel.expressions
        )
        for t in tables:
            for col in sorted(pii_columns.get(t, set())):
                if star or col.lower() in referenced:
                    pii_hits.append(f"{t}.{col}")

    # Enforce LIMIT (the executor also caps fetched rows: belt and braces).
    assert isinstance(root, exp.Query)
    limit = root.args.get("limit")
    current = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        try:
            current = int(str(limit.expression.this))
        except ValueError:
            current = None
    if current is None or current > max_rows:
        root = root.limit(max_rows)

    return GuardResult(ok=True, sql=root.sql(dialect=dialect), tables=tables, pii_columns=pii_hits)
