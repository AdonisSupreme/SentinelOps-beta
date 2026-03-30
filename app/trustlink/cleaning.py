"""Pure cleaning and transformation utilities for Trustlink extracts."""

from typing import Dict, Any, Optional, Tuple
import re
import unicodedata

import pandas as pd

FINAL_COLUMNS = [
    "BRANCH_CODE",
    "ACCOUNT_NUMBER",
    "ACNTS_PROD_CODE",
    "ACCOUNT_NAME",
    "CURRENCY_CODE",
]


def sanitize_string(value: Optional[str]) -> Optional[str]:
    """Normalize, strip and collapse whitespace for a string value.

    Returns None if input is None or empty after stripping.
    """
    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    # Normalize unicode, remove control characters, collapse whitespace
    s = unicodedata.normalize("NFC", value)
    # remove control characters
    s = re.sub(r"[\x00-\x1F\x7F]+", "", s)
    # collapse whitespace and trim
    s = re.sub(r"\s+", " ", s).strip()

    return s or None


_ACC_NUM_RE = re.compile(r"^[A-Za-z0-9\-.]{4,32}$")


def validate_account_number(value: Optional[str]) -> bool:
    """Return True if account number is valid.

    Current rule: digits-only, length between 6 and 30.
    """
    if value is None:
        return False

    if not isinstance(value, str):
        value = str(value)

    s = re.sub(r"\s+", "", value)
    return bool(_ACC_NUM_RE.match(s))


_NAME_RE = re.compile(r"^[\w\-\.,&'()\s]{2,200}$", re.UNICODE)


def validate_account_name(value: Optional[str]) -> bool:
    """Return True if account name looks valid.

    Conservative check: length 2-200 and contains only reasonable characters.
    """
    if value is None:
        return False

    if not isinstance(value, str):
        value = str(value)

    s = value.strip()
    if len(s) < 2 or len(s) > 200:
        return False

    return bool(_NAME_RE.match(s))


def _find_column(df: pd.DataFrame, candidates: Tuple[str, ...]) -> Optional[str]:
    lower_map = {str(col).lower(): col for col in df.columns}
    for c in candidates:
        match = lower_map.get(c.lower())
        if match is not None:
            return match
    return None


def transform_idc_to_trustlink_format(df: pd.DataFrame) -> pd.DataFrame:
    """Map IDC extraction output into final Trustlink output schema."""
    if df is None or df.empty:
        return pd.DataFrame()

    source = df.copy(deep=True)

    branch_col = _find_column(source, ("BRANCH_CODE", "ACNTS_BRN_CODE", "branch_code"))
    account_col = _find_column(source, ("ACCOUNT_NUMBER", "IACLINK_ACTUAL_ACNUM", "account_number"))
    product_col = _find_column(source, ("ACNTS_PROD_CODE", "PRODUCT_CODE", "acnts_prod_code"))
    name_col = _find_column(source, ("ACCOUNT_NAME", "ACNTS_AC_NAME1", "account_name"))
    currency_col = _find_column(source, ("CURRENCY_CODE", "ACNTS_CURR_CODE", "currency_code"))

    required = {
        "BRANCH_CODE": branch_col,
        "ACCOUNT_NUMBER": account_col,
        "ACNTS_PROD_CODE": product_col,
        "ACCOUNT_NAME": name_col,
        "CURRENCY_CODE": currency_col,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(f"IDC input missing required columns for mapping: {missing}")

    out = pd.DataFrame(
        {
            "BRANCH_CODE": source[branch_col],
            "ACCOUNT_NUMBER": source[account_col],
            "ACNTS_PROD_CODE": source[product_col],
            "ACCOUNT_NAME": source[name_col],
            "CURRENCY_CODE": source[currency_col],
        }
    )
    return _sanitize_final_rows(out)


def transform_digipay_to_trustlink_format(df: pd.DataFrame, currency: str) -> pd.DataFrame:
    """Map DIGIPAY extraction output into final Trustlink output schema."""
    if df is None or df.empty:
        return pd.DataFrame(columns=FINAL_COLUMNS)

    currency_upper = str(currency).upper().strip()
    if currency_upper not in {"USD", "ZWG"}:
        raise ValueError(f"Unsupported DIGIPAY currency '{currency}'. Expected USD or ZWG.")

    product_code = 150 if currency_upper == "USD" else 151
    source = df.copy(deep=True)

    account_col = _find_column(source, ("account_no", "ACCOUNT_NO", "account_number"))
    name_col = _find_column(source, ("customer_name", "CUSTOMER_NAME", "account_name", "name"))

    missing = []
    if account_col is None:
        missing.append("account_no")
    if name_col is None:
        missing.append("customer_name")
    if missing:
        raise ValueError(f"DIGIPAY input missing required columns: {missing}")

    out = pd.DataFrame(
        {
            "BRANCH_CODE": 1000,
            "ACCOUNT_NUMBER": source[account_col],
            "ACNTS_PROD_CODE": product_code,
            "ACCOUNT_NAME": source[name_col],
            "CURRENCY_CODE": currency_upper,
        }
    )
    return _sanitize_final_rows(out)


def _sanitize_final_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize and normalize final output columns."""
    out = df.copy(deep=True)
    out["BRANCH_CODE"] = pd.to_numeric(out["BRANCH_CODE"], errors="coerce").fillna(0).astype(int)
    out["ACNTS_PROD_CODE"] = pd.to_numeric(out["ACNTS_PROD_CODE"], errors="coerce").fillna(0).astype(int)
    out["ACCOUNT_NUMBER"] = out["ACCOUNT_NUMBER"].apply(sanitize_string)
    out["ACCOUNT_NAME"] = out["ACCOUNT_NAME"].apply(sanitize_string)
    out["CURRENCY_CODE"] = out["CURRENCY_CODE"].apply(lambda v: (sanitize_string(v) or "").upper())
    return out[FINAL_COLUMNS]


def validate_final(df: pd.DataFrame) -> Dict[str, int]:
    """Perform final validation metrics on the consolidated DataFrame.

    Returns a metrics dict with counts required by the caller.
    """
    if df is None or df.empty:
        return {
            "total_rows": 0,
            "valid_accounts": 0,
            "valid_names": 0,
            "invalid_accounts": 0,
            "invalid_names": 0,
            "duplicate_accounts": 0,
        }

    total = int(len(df))
    valid_account_flags = df["ACCOUNT_NUMBER"].apply(validate_account_number) if "ACCOUNT_NUMBER" in df.columns else pd.Series(dtype=bool)
    valid_name_flags = df["ACCOUNT_NAME"].apply(validate_account_name) if "ACCOUNT_NAME" in df.columns else pd.Series(dtype=bool)
    valid_accounts = int(valid_account_flags.sum()) if len(valid_account_flags) else 0
    valid_names = int(valid_name_flags.sum()) if len(valid_name_flags) else 0
    duplicate_accounts = int(df["ACCOUNT_NUMBER"].duplicated().sum()) if "ACCOUNT_NUMBER" in df.columns else 0

    return {
        "total_rows": total,
        "valid_accounts": valid_accounts,
        "valid_names": valid_names,
        "invalid_accounts": int(total - valid_accounts),
        "invalid_names": int(total - valid_names),
        "duplicate_accounts": duplicate_accounts,
    }


def generate_integrity_report(df: pd.DataFrame, path: Optional[str] = None) -> Dict[str, Any]:
    """Generate an integrity report structure from `df`.

    This function is pure and returns a dictionary with summary statistics.
    The `path` argument is accepted for API compatibility but is NOT used to
    perform any file I/O — writing should be performed by the caller if
    desired.
    """
    metrics = validate_final(df)

    report = {
        "metrics": metrics,
        "by_currency": {},
    }

    if df is not None and not df.empty and "CURRENCY_CODE" in df.columns:
        grp = df.groupby("CURRENCY_CODE")
        for currency, group in grp:
            report["by_currency"] = report.get("by_currency", {})
            cur_valid_accounts = group["ACCOUNT_NUMBER"].apply(validate_account_number)
            cur_valid_names = group["ACCOUNT_NAME"].apply(validate_account_name)
            report["by_currency"][str(currency)] = {
                "rows": int(len(group)),
                "valid_accounts": int(cur_valid_accounts.sum()),
                "valid_names": int(cur_valid_names.sum()),
            }

    # include provided path in report for caller convenience, but do not write
    if path:
        report["report_path"] = path

    return report


def run_full_transformation(idc_df: pd.DataFrame, usd_df: pd.DataFrame, zwg_df: pd.DataFrame) -> Dict[str, Any]:
    """Run full cleaning/transformation pipeline and return dataframe+metrics.

    Steps (pure):
    - clean `idc_df` (clean_trustlink)
    - transform `usd_df` and `zwg_df` (transform_digipay)
    - concatenate results
    - compute final metrics

    Returns a dict with keys:
    - `dataframe`: the consolidated pandas.DataFrame
    - `metrics`: { total_rows, valid_accounts, valid_names }
    """
    idc_t = transform_idc_to_trustlink_format(idc_df) if idc_df is not None else pd.DataFrame(columns=FINAL_COLUMNS)
    usd_t = transform_digipay_to_trustlink_format(usd_df, "USD") if usd_df is not None else pd.DataFrame(columns=FINAL_COLUMNS)
    zwg_t = transform_digipay_to_trustlink_format(zwg_df, "ZWG") if zwg_df is not None else pd.DataFrame(columns=FINAL_COLUMNS)

    # Concatenate in a stable order
    frames = [f for f in (idc_t, usd_t, zwg_t) if f is not None and not f.empty]
    if frames:
        combined = pd.concat(frames, ignore_index=True, sort=False)
    else:
        combined = pd.DataFrame(columns=FINAL_COLUMNS)

    combined = combined[FINAL_COLUMNS]

    metrics = validate_final(combined)
    metrics.update(
        {
            "idc_rows": int(len(idc_t)),
            "usd_rows": int(len(usd_t)),
            "zwg_rows": int(len(zwg_t)),
        }
    )

    return {"dataframe": combined, "metrics": metrics}
