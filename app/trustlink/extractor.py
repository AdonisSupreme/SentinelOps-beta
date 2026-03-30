"""Trustlink data extractors.

Functions:
- extract_idc_accounts(): runs an Oracle query and returns a pandas.DataFrame
- extract_digipay_accounts(): runs a Postgres query and returns a DataFrame
- split_digipay(df): splits a digipay DataFrame by currency code

Notes / rules enforced by implementation:
- No file writing
- No logging side effects
- Raise exceptions on failure
"""

import os
import time
from time import perf_counter
from typing import Optional, Tuple, Dict, Any

import pandas as pd
import psycopg

DEFAULT_IDC_QUERY = """
SELECT DISTINCT
    A.ACNTS_BRN_CODE BRANCH_CODE,
    I.IACLINK_ACTUAL_ACNUM ACCOUNT_NUMBER,
    A.ACNTS_PROD_CODE,
    (A.ACNTS_AC_NAME1 || A.ACNTS_AC_NAME2) ACCOUNT_NAME,
    A.ACNTS_CURR_CODE CURRENCY_CODE
FROM AFCPRODIDC.ACNTBAL AC,
     AFCPRODIDC.ACNTS A,
     AFCPRODIDC.IACLINK I,
     AFCPRODIDC.PRODUCTS P
WHERE AC.ACNTBAL_INTERNAL_ACNUM = A.ACNTS_INTERNAL_ACNUM
  AND A.ACNTS_INTERNAL_ACNUM = I.IACLINK_INTERNAL_ACNUM
  AND A.ACNTS_CLOSURE_DATE IS NULL
ORDER BY ACNTS_BRN_CODE, ACNTS_PROD_CODE
"""

DEFAULT_DIGIPAY_QUERY = """
SELECT
    cm.first_name || ' ' || cm.last_name AS customer_name,
    cua.account_no,
    csm.code
FROM customer_master cm
    JOIN casa_account_master ca ON cm.id = ca.customer_id
    LEFT JOIN customer_account cua ON cua.account_no = ca.account_no
    LEFT JOIN account_status ats ON ca.status = ats.id
    LEFT JOIN casa_scheme_master csm ON csm.id = ca.scheme_id
    LEFT JOIN application_user au1 ON au1.id = ca.changed_by
WHERE csm.description = 'DIGIPAY'
  AND ats.status_type = 'Active'
  AND ca.opened_date >= DATE '2022-01-01'
"""

USD_SCHEME_CODE = "2001001"
ZWG_SCHEME_CODE = "2002002"


def _is_retryable_oracle_error(exc: Exception) -> bool:
    message = str(exc).lower()
    retryable_markers = (
        "dpy-6005",
        "ora-12170",
        "ora-12541",
        "ora-12545",
        "ora-12514",
        "ora-12537",
        "timed out",
        "timeout",
        "cannot connect",
        "connection refused",
        "connection reset",
        "network is unreachable",
    )
    return any(marker in message for marker in retryable_markers)


def _get_oracle_module():
    try:
        import oracledb as oracledb_module
        return oracledb_module
    except Exception:
        try:
            import cx_Oracle as oracledb_module
            return oracledb_module
        except Exception:
            raise ImportError(
                "Oracle DB driver not available (install 'oracledb' or 'cx_Oracle')"
            )


def _get_oracle_connection():
    """Create and return an Oracle DB connection.

    The helper reads connection parameters from environment variables. Accepts either
    a full DSN via `ORACLE_DSN` or individual pieces: `ORACLE_USER`, `ORACLE_PASSWORD`,
    `IDC_ORACLE_HOST`, `ORACLE_PORT`, plus either `ORACLE_SERVICE`/`ORACLE_SERVICE_NAME`
    or `ORACLE_SID`.
    """
    oracledb = _get_oracle_module()

    user = os.getenv("ORACLE_USER")
    password = os.getenv("ORACLE_PASSWORD")
    dsn = os.getenv("ORACLE_DSN")
    connect_timeout = float(os.getenv("ORACLE_CONNECT_TIMEOUT_SECONDS", "30"))

    # Build connection kwargs once so we can handle both DSN and host/service paths.
    conn_kwargs: Dict[str, Any] = {}
    if user:
        conn_kwargs["user"] = user
    if password:
        conn_kwargs["password"] = password

    if dsn:
        conn_kwargs["dsn"] = dsn
        # python-oracledb supports tcp_connect_timeout in thin mode.
        if "oracledb" in getattr(oracledb, "__name__", ""):
            conn_kwargs["tcp_connect_timeout"] = connect_timeout
        return oracledb.connect(**conn_kwargs)

    host = os.getenv("IDC_ORACLE_HOST")
    port = os.getenv("ORACLE_PORT", "1521")
    service_name = os.getenv("ORACLE_SERVICE_NAME") or os.getenv("ORACLE_SERVICE")
    sid = os.getenv("ORACLE_SID")

    if not (user and password and host and (service_name or sid)):
        raise ValueError(
            "Oracle connection information missing in environment. "
            "Set ORACLE_DSN or IDC_ORACLE_HOST/ORACLE_PORT with ORACLE_SERVICE_NAME "
            "(or ORACLE_SERVICE) or ORACLE_SID."
        )

    if hasattr(oracledb, "makedsn"):
        if sid:
            dsn = oracledb.makedsn(host=host, port=int(port), sid=sid)
        else:
            dsn = oracledb.makedsn(host=host, port=int(port), service_name=service_name)
    else:
        dsn = f"{host}:{port}/{service_name}" if service_name else f"{host}:{port}/{sid}"

    conn_kwargs["dsn"] = dsn
    if "oracledb" in getattr(oracledb, "__name__", ""):
        conn_kwargs["tcp_connect_timeout"] = connect_timeout
    return oracledb.connect(**conn_kwargs)


def _get_digipay_postgres_connection():
    """Create and return a Postgres connection for DIGIPAY extraction.

    Preference order:
    1) DIGIPAY_DATABASE_URL
    2) DIGIPAY_* / POSTGRES_* discrete credentials
    3) DATABASE_URL (working default fallback)
    """
    connect_timeout = int(os.getenv("DIGIPAY_CONNECT_TIMEOUT_SECONDS", "15"))
    statement_timeout_ms = int(os.getenv("DIGIPAY_STATEMENT_TIMEOUT_MS", "60000"))
    connect_kwargs: Dict[str, Any] = {
        "connect_timeout": connect_timeout,
        "options": f"-c statement_timeout={statement_timeout_ms}",
    }

    dsn = (os.getenv("DIGIPAY_DATABASE_URL") or "").strip()
    if dsn:
        return psycopg.connect(dsn, **connect_kwargs)

    # Use discrete params to safely handle special characters in password (e.g. '@').
    user = (
        os.getenv("DIGIPAY_PG_USER")
        or os.getenv("DIGIPAY_POSTGRES_USER")
        or os.getenv("POSTGRES_USER")
    )
    password = (
        os.getenv("DIGIPAY_PG_PASSWORD")
        or os.getenv("DIGIPAY_POSTGRES_PASSWORD")
        or os.getenv("POSTGRES_PASSWORD")
    )
    host = (
        os.getenv("DIGIPAY_PG_HOST")
        or os.getenv("DIGIPAY_POSTGRES_HOST")
        or os.getenv("POSTGRES_HOST")
    )
    port = (
        os.getenv("DIGIPAY_PG_PORT")
        or os.getenv("DIGIPAY_POSTGRES_PORT")
        or os.getenv("POSTGRES_PORT")
        or "5432"
    )
    database = (
        os.getenv("DIGIPAY_PG_DATABASE")
        or os.getenv("DIGIPAY_POSTGRES_DATABASE")
        or os.getenv("POSTGRES_DATABASE")
        or os.getenv("POSTGRES_DB")
    )

    if user and password and host and database:
        return psycopg.connect(
            host=host.strip(),
            port=int(str(port).strip()),
            dbname=database.strip(),
            user=user.strip(),
            password=password,
            **connect_kwargs,
        )

    # Mirror working app behavior from config.py.
    fallback_dsn = os.getenv(
        "DATABASE_URL",
        "postgresql://data_warehouse:warehouse@2025@192.168.0.119:5432/ewallet_prod",
    ).strip()
    print(f"DIGIPAY SCHEMA: {database}")
    if fallback_dsn:
        return psycopg.connect(fallback_dsn, **connect_kwargs)

    raise ValueError(
        "Digipay Postgres connection is not configured. Set DIGIPAY_DATABASE_URL "
        "or DIGIPAY_PG_USER/DIGIPAY_PG_PASSWORD/DIGIPAY_PG_HOST/DIGIPAY_PG_DATABASE."
    )


def extract_idc_accounts(query: Optional[str] = None) -> pd.DataFrame:
    """Run the IDC query against Oracle and return a pandas DataFrame.

    The SQL must be provided either via the `query` argument or the
    environment variable `TRUSTLINK_IDC_QUERY`. The query is executed exactly
    as provided and the raw results are returned as a DataFrame.
    """
    sql = query or os.getenv("TRUSTLINK_IDC_QUERY") or DEFAULT_IDC_QUERY

    max_attempts = int(os.getenv("ORACLE_CONNECT_RETRIES", "4"))
    backoff_seconds = float(os.getenv("ORACLE_CONNECT_RETRY_DELAY_SECONDS", "3"))

    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        conn = None
        cur = None
        try:
            conn = _get_oracle_connection()
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return pd.DataFrame(rows, columns=cols)
        except Exception as exc:
            last_error = exc
            is_retryable = _is_retryable_oracle_error(exc)
            if attempt < max_attempts and is_retryable:
                time.sleep(backoff_seconds * attempt)
                continue
            raise
        finally:
            try:
                if cur is not None:
                    cur.close()
            except Exception:
                pass
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    if last_error:
        raise last_error
    raise RuntimeError("IDC extraction failed without a specific error")


def extract_digipay_accounts(query: Optional[str] = None) -> pd.DataFrame:
    """Run the DIGIPAY query against Postgres and return a pandas DataFrame.

    The SQL must be provided either via the `query` argument or the
    environment variable `TRUSTLINK_DIGIPAY_QUERY`. The query is executed
    exactly as provided and the raw results are returned as a DataFrame.
    """
    sql = query or os.getenv("TRUSTLINK_DIGIPAY_QUERY") or DEFAULT_DIGIPAY_QUERY

    max_attempts = int(os.getenv("DIGIPAY_CONNECT_RETRIES", "3"))
    backoff_seconds = float(os.getenv("DIGIPAY_CONNECT_RETRY_DELAY_SECONDS", "2"))

    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        conn = None
        try:
            conn = _get_digipay_postgres_connection()
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                return pd.DataFrame(rows, columns=cols)
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            is_retryable = (
                "timeout" in message
                or "timed out" in message
                or "could not connect" in message
                or "could not translate host name" in message
                or "temporary failure in name resolution" in message
                or "connection refused" in message
                or "connection reset" in message
                or "network is unreachable" in message
            )
            if attempt < max_attempts and is_retryable:
                time.sleep(backoff_seconds * attempt)
                continue
            raise
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    if last_error:
        raise last_error
    raise RuntimeError("DIGIPAY extraction failed without a specific error")


def split_digipay(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split a Digipay DataFrame into USD and ZWG DataFrames.

    Currency codes:
    - USD => 2001001
    - ZWG => 2002002

    The function looks for a currency column named `currency_code` or `code`.
    It raises ValueError if no such column is found.
    """
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()

    if "currency_code" in df.columns:
        col = "currency_code"
    elif "code" in df.columns:
        col = "code"
    else:
        raise ValueError("DataFrame must contain a currency column named 'currency_code' or 'code'")

    code_series = df[col].astype(str).str.strip()
    usd_mask = code_series == USD_SCHEME_CODE
    zwg_mask = code_series == ZWG_SCHEME_CODE

    unknown = code_series[~(usd_mask | zwg_mask)].dropna().unique().tolist()
    if unknown:
        raise ValueError(
            f"Unknown DIGIPAY scheme code(s) found: {unknown}. "
            f"Expected only {USD_SCHEME_CODE} (USD) and {ZWG_SCHEME_CODE} (ZWG)."
        )

    usd = df.loc[usd_mask].copy()
    zwg = df.loc[zwg_mask].copy()

    return usd, zwg


def extract_all_with_metrics(
    idc_query: Optional[str] = None,
    digipay_query: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract IDC and DIGIPAY sources and return DataFrames + extraction metrics."""
    t0 = perf_counter()
    idc_df = extract_idc_accounts(query=idc_query)
    idc_ms = int((perf_counter() - t0) * 1000)

    t1 = perf_counter()
    digipay_df = extract_digipay_accounts(query=digipay_query)
    digipay_ms = int((perf_counter() - t1) * 1000)

    usd_df, zwg_df = split_digipay(digipay_df)

    return {
        "idc_df": idc_df,
        "digipay_df": digipay_df,
        "usd_df": usd_df,
        "zwg_df": zwg_df,
        "metrics": {
            "idc_rows": int(len(idc_df)),
            "digipay_rows": int(len(digipay_df)),
            "usd_rows": int(len(usd_df)),
            "zwg_rows": int(len(zwg_df)),
            "idc_extract_duration_ms": idc_ms,
            "digipay_extract_duration_ms": digipay_ms,
        },
    }
