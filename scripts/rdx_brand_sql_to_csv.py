"""
Convert rdx_brand.sql INSERT data to rdx_brand.csv
Usage: python scripts/rdx_brand_sql_to_csv.py [path/to/rdx_brand.sql]
"""

import csv
import re
import sys
from pathlib import Path

DEFAULT_SQL_PATH = Path.home() / (
    "Library/Containers/com.tencent.xinWeChat/Data/Documents/"
    "xwechat_files/wxid_uc27rz9y5s6f22_6089/temp/drag/rdx_brand.sql"
)
OUTPUT_CSV = Path(__file__).parent.parent / "data" / "rdx_brand.csv"

COLUMNS = [
    "id", "admin_id", "name_jp", "name_en", "name_kana", "name_cn",
    "status", "rakuten_id", "yshop_id", "yauc_id", "meru_id", "ebay_id",
    "rakuma_id", "amazon_id", "amazon_name", "qoo10_id", "qoo10_name",
    "pid", "sort", "icon", "merchant_code", "source", "add_time",
]

KEEP_COLUMNS = [
    "id", "name_jp", "name_en", "name_cn",
    "rakuten_id", "yshop_id", "yauc_id", "meru_id",
    "ebay_id", "rakuma_id", "amazon_id", "qoo10_id",
]


def parse_value_token(token: str):
    """Convert a single SQL token to a Python value."""
    token = token.strip()
    if token.upper() == "NULL":
        return ""
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1].replace("''", "'").replace("\\'", "'")
    return token


def parse_row(row_str: str) -> list[str]:
    """
    Parse one SQL row like: (val1, 'val2', NULL, ...)
    Handles quoted strings with commas / escaped quotes inside them.
    """
    row_str = row_str.strip()
    if row_str.startswith("("):
        row_str = row_str[1:]
    if row_str.endswith(")"):
        row_str = row_str[:-1]

    tokens: list[str] = []
    current: list[str] = []
    in_quote = False
    i = 0
    while i < len(row_str):
        ch = row_str[i]
        if ch == "'" and not in_quote:
            in_quote = True
            current.append(ch)
        elif ch == "'" and in_quote:
            if i + 1 < len(row_str) and row_str[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            else:
                in_quote = False
                current.append(ch)
        elif ch == "\\" and in_quote and i + 1 < len(row_str):
            current.append(ch)
            current.append(row_str[i + 1])
            i += 2
            continue
        elif ch == "," and not in_quote:
            tokens.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1

    if current:
        tokens.append("".join(current).strip())

    return [parse_value_token(t) for t in tokens]


def sql_to_csv(sql_path: Path, csv_path: Path) -> int:
    """Parse sql_path and write CSV to csv_path. Returns number of rows written."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    insert_header_re = re.compile(
        r"INSERT INTO `rdx_brand`[^V]+VALUES\s*$", re.IGNORECASE
    )

    rows_written = 0

    with (
        sql_path.open("r", encoding="utf-8", errors="replace") as f,
        csv_path.open("w", newline="", encoding="utf-8-sig") as out,
    ):
        keep_idx = [COLUMNS.index(c) for c in KEEP_COLUMNS]
        writer = csv.writer(out)
        writer.writerow(KEEP_COLUMNS)

        inside_insert = False
        pending: list[str] = []

        def flush_pending():
            nonlocal rows_written
            if not pending:
                return
            row_str = "".join(pending).rstrip().rstrip(",").rstrip(";")
            values = parse_row(row_str)
            if len(values) == len(COLUMNS):
                writer.writerow([values[i] for i in keep_idx])
                rows_written += 1
            elif values and any(v for v in values):
                values += [""] * (len(COLUMNS) - len(values))
                writer.writerow([values[i] for i in keep_idx])
                rows_written += 1
            pending.clear()

        for line in f:
            stripped = line.rstrip("\n")

            if insert_header_re.search(stripped):
                inside_insert = True
                pending.clear()
                continue

            if not inside_insert:
                continue

            if stripped.strip() in ("", "--") or stripped.strip().startswith("--"):
                flush_pending()
                inside_insert = False
                continue

            stripped_s = stripped.strip()

            if stripped_s.startswith("("):
                flush_pending()
                pending.append(stripped_s)
            else:
                pending.append(stripped_s)

            if stripped_s.endswith(");"):
                flush_pending()
                inside_insert = False
            elif stripped_s.endswith("),") or stripped_s.endswith(")"):
                flush_pending()

        flush_pending()

    return rows_written


def main():
    sql_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SQL_PATH
    if not sql_path.exists():
        print(f"[ERROR] SQL file not found: {sql_path}")
        sys.exit(1)

    print(f"[INFO] Reading : {sql_path}")
    print(f"[INFO] Writing : {OUTPUT_CSV}")

    count = sql_to_csv(sql_path, OUTPUT_CSV)
    print(f"[OK]   {count} rows written to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
