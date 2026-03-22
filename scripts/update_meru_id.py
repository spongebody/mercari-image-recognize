"""
Update meru_id and zenplus_id fields in data/rdx_category.csv.

  - meru_id   ← suggested_target_id  from data/rakuten_to_mercari.csv
                 (matched on id == source_id)
  - zenplus_id ← ZenPlus Category ID from data/Rakuten_ZenPlus_Catetory_Mapping.csv
                 (matched on id == Rakuten Category ID)

Usage: python scripts/update_meru_id.py
"""

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
RDX_CSV  = BASE_DIR / "data" / "rdx_category.csv"
RAK_CSV  = BASE_DIR / "data" / "rakuten_to_mercari.csv"
ZP_CSV   = BASE_DIR / "data" / "Rakuten_ZenPlus_Catetory_Mapping.csv"


def load_meru_mapping(csv_path: Path) -> dict[str, str]:
    """Build { source_id: suggested_target_id } from rakuten_to_mercari.csv."""
    with csv_path.open(encoding="utf-8-sig") as f:
        return {row["source_id"]: row["suggested_target_id"] for row in csv.DictReader(f)}


def load_zenplus_mapping(csv_path: Path) -> dict[str, str]:
    """Build { rakuten_id: zenplus_id } from Rakuten_ZenPlus_Catetory_Mapping.csv."""
    with csv_path.open(encoding="utf-8-sig") as f:
        return {
            row["Rakuten Category ID"]: row["ZenPlus Category ID"]
            for row in csv.DictReader(f)
        }


def apply_updates(
    rdx_path: Path,
    meru_mapping: dict[str, str],
    zp_mapping: dict[str, str],
) -> tuple[dict, dict]:
    """
    Apply meru_id and zenplus_id updates to rdx_category.csv in-place.
    Returns (meru_stats, zp_stats).
    """
    with rdx_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    def empty_stats(total: int) -> dict:
        return {"total": total, "updated": 0, "unchanged": 0, "skipped": 0, "skipped_ids": []}

    meru_stats = empty_stats(len(rows))
    zp_stats   = empty_stats(len(rows))

    for row in rows:
        rid = row["id"]

        # --- meru_id ---
        new_meru = meru_mapping.get(rid)
        if new_meru is None:
            meru_stats["skipped"] += 1
            meru_stats["skipped_ids"].append(rid)
        elif new_meru != row["meru_id"]:
            row["meru_id"] = new_meru
            meru_stats["updated"] += 1
        else:
            meru_stats["unchanged"] += 1

        # --- zenplus_id ---
        new_zp = zp_mapping.get(rid)
        if new_zp is None:
            zp_stats["skipped"] += 1
            zp_stats["skipped_ids"].append(rid)
        elif new_zp != row["zenplus_id"]:
            row["zenplus_id"] = new_zp
            zp_stats["updated"] += 1
        else:
            zp_stats["unchanged"] += 1

    with rdx_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return meru_stats, zp_stats


def print_stats(label: str, stats: dict) -> None:
    width = 44
    print()
    print("=" * width)
    print(f"  {label} Update Statistics")
    print("=" * width)
    print(f"  Total rows processed     : {stats['total']:>6}")
    print(f"  Updated (value changed)  : {stats['updated']:>6}")
    print(f"  Already correct          : {stats['unchanged']:>6}")
    print(f"  No match (skipped)       : {stats['skipped']:>6}")
    if stats["skipped_ids"]:
        # Print in batches of 10 for readability
        ids = stats["skipped_ids"]
        for i in range(0, min(len(ids), 30), 10):
            chunk = ", ".join(ids[i:i+10])
            prefix = "    Skipped IDs:" if i == 0 else "               "
            print(f"{prefix} {chunk}")
        if len(ids) > 30:
            print(f"               ... and {len(ids) - 30} more")
    print("=" * width)


def main():
    for path in (RDX_CSV, RAK_CSV, ZP_CSV):
        if not path.exists():
            print(f"[ERROR] File not found: {path}", file=sys.stderr)
            sys.exit(1)

    print(f"[INFO] Loading meru_id mapping    : {RAK_CSV.name}")
    meru_mapping = load_meru_mapping(RAK_CSV)
    print(f"       {len(meru_mapping)} entries loaded")

    print(f"[INFO] Loading zenplus_id mapping : {ZP_CSV.name}")
    zp_mapping = load_zenplus_mapping(ZP_CSV)
    print(f"       {len(zp_mapping)} entries loaded")

    print(f"\n[INFO] Applying updates to        : {RDX_CSV.name}")
    meru_stats, zp_stats = apply_updates(RDX_CSV, meru_mapping, zp_mapping)

    print_stats("meru_id", meru_stats)
    print_stats("zenplus_id", zp_stats)

    total_updated = meru_stats["updated"] + zp_stats["updated"]
    print(f"\n[OK]   {RDX_CSV} updated — {total_updated} field(s) changed in total.")


if __name__ == "__main__":
    main()
