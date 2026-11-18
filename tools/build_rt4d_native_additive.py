from __future__ import annotations

import argparse
import csv
import hashlib
import re
import struct
from collections import defaultdict
from pathlib import Path


CHANNEL_BASE = 0x4000
CHANNEL_SIZE = 0x30
CHANNEL_COUNT = 1024
ZONE_BASE = 0x1E000
ZONE_SIZE = 0x208
ZONE_COUNT = 512
ZONE_MEMBER_LIMIT = 200


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def verified_analog_candidates(master: list[dict[str, str]]) -> list[dict[str, str]]:
    rows = []
    for row in master:
        modes = f"{row['mode_source']} {row['mode_crosscheck']}".upper()
        if row["transmit_ready"] != "YES":
            continue
        if "FM" not in modes:
            continue
        if any(item in modes for item in ("DMR", "D-STAR", "P-25", "NXDN")):
            continue
        if not re.fullmatch(r"\d{2,3}\.\d", row["access_crosscheck"].strip()):
            continue
        rows.append(row)
    rows.sort(
        key=lambda row: (
            int(row["priority"]),
            float(row["approx_distance_miles"]) if row["approx_distance_miles"] else 9999,
            row["region"],
            row["location"],
            float(row["rx_mhz"]),
        )
    )
    return rows


def decode_alias(record: bytes) -> str:
    return record[32:48].rstrip(b"\xff\x00 ").decode("ascii", "replace")


def frequency_pair(row: dict[str, str]) -> tuple[int, int]:
    return (
        round(float(row["rx_mhz"]) * 100000),
        round(float(row["tx_mhz"]) * 100000),
    )


def make_alias(row: dict[str, str], used: set[str]) -> str:
    call = re.sub(r"[^A-Z0-9]", "", row["callsign"].split()[0].upper())[:7]
    city = re.sub(r"[^A-Z0-9]", "", row["location"].upper())[:5]
    band = "V" if row["band"] == "2m" else "U"
    base = f"{call} {city} {band}"[:16].strip()
    alias = base
    serial = 2
    while alias in used:
        suffix = str(serial)
        alias = f"{base[:16-len(suffix)]}{suffix}"
        serial += 1
    used.add(alias)
    return alias


def encode_ctcss(tone: str) -> int:
    return 0x1000 | round(float(tone) * 10)


def make_channel_record(row: dict[str, str], alias: str) -> bytes:
    record = bytearray(0x30)
    # CPS V2 analogue, TX enabled, wide FM. This five-byte prefix and the
    # settings tail match a CPS V2-generated analogue channel from the same
    # radio family. Frequencies are stored as MHz * 100000, little endian.
    record[0:5] = bytes((0x40, 0x10, 0x4E, 0x01, 0x00))
    rx, tx = frequency_pair(row)
    struct.pack_into("<I", record, 5, rx)
    struct.pack_into("<I", record, 9, tx)
    # Leave receive squelch open. Send the verified repeater access tone.
    struct.pack_into("<H", record, 13, 0)
    struct.pack_into("<H", record, 15, encode_ctcss(row["access_crosscheck"]))
    record[22] = 0x01
    record[32:48] = alias.encode("ascii").ljust(16, b"\xff")
    return bytes(record)


def make_zone_record(name: str, channel_numbers: list[int]) -> bytes:
    if not channel_numbers or len(channel_numbers) > ZONE_MEMBER_LIMIT:
        raise ValueError(f"Invalid zone member count for {name}: {len(channel_numbers)}")
    record = bytearray(b"\xff" * ZONE_SIZE)
    record[0:2] = b"\x00\x00"
    struct.pack_into("<H", record, 2, channel_numbers[0] - 1)
    record[4:20] = name.encode("ascii").ljust(16, b"\xff")
    for index, channel in enumerate(channel_numbers):
        struct.pack_into("<H", record, 20 + index * 2, channel - 1)
    return bytes(record)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add verified analogue Wisconsin repeaters to a CPS V2 RT-4D native file"
    )
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    original = args.native.read_bytes()
    if len(original) != 0x100000:
        raise RuntimeError(f"Unexpected RT-4D native size: {len(original)}")

    occupied: list[dict] = []
    occupied_analog_pairs: dict[tuple[int, int], list[dict]] = defaultdict(list)
    used_aliases: set[str] = set()
    for index in range(CHANNEL_COUNT):
        start = CHANNEL_BASE + index * CHANNEL_SIZE
        record = original[start:start + CHANNEL_SIZE]
        alias = decode_alias(record)
        rx = struct.unpack_from("<I", record, 5)[0]
        tx = struct.unpack_from("<I", record, 9)[0]
        if not alias or rx in (0, 0xFFFFFFFF):
            continue
        item = {
            "channel": index + 1,
            "alias": alias,
            "rx_raw": rx,
            "tx_raw": tx,
            "record_byte_0": record[0],
        }
        occupied.append(item)
        used_aliases.add(alias)
        if record[0] == 0x50:
            occupied_analog_pairs[(rx, tx)].append(item)

    highest = max(item["channel"] for item in occupied)
    if any(item["channel"] > highest for item in occupied):
        raise AssertionError("Occupied-channel scan failed")

    candidates = verified_analog_candidates(load_csv(args.master))
    skipped = []
    additions = []
    next_channel = highest + 1
    for row in candidates:
        pair = frequency_pair(row)
        matches = occupied_analog_pairs.get(pair, [])
        if matches:
            skipped.append({
                "callsign": row["callsign"],
                "location": row["location"],
                "region": row["region"],
                "rx_mhz": row["rx_mhz"],
                "tx_mhz": row["tx_mhz"],
                "access": row["access_crosscheck"],
                "existing_channels": ";".join(str(item["channel"]) for item in matches),
                "existing_aliases": ";".join(item["alias"] for item in matches),
                "handling": "Skipped: same analogue RX/TX pair already exists in current native file",
            })
            continue
        if next_channel > CHANNEL_COUNT:
            raise RuntimeError("RT-4D channel bank is full")
        alias = make_alias(row, used_aliases)
        additions.append({
            **row,
            "channel": next_channel,
            "alias": alias,
        })
        next_channel += 1

    result = bytearray(original)
    for row in additions:
        start = CHANNEL_BASE + (row["channel"] - 1) * CHANNEL_SIZE
        result[start:start + CHANNEL_SIZE] = make_channel_record(row, row["alias"])

    region_names = {
        "Central Wisconsin": "WI CENTRAL",
        "Northern Wisconsin": "WI NORTH",
        "Northeastern Wisconsin": "WI NORTHEAST",
        "South-central Wisconsin": "WI SOUTHCENT",
        "Southeastern Wisconsin": "WI SOUTHEAST",
        "Western Wisconsin": "WI WEST",
    }
    zones: list[tuple[str, list[int]]] = []
    nearby = [
        row["channel"] for row in additions
        if row["distance_group"] in ("Within 25 miles", "25-50 miles")
    ]
    if nearby:
        zones.append(("WI NEARBY", nearby))
    for region, zone_name in region_names.items():
        members = [row["channel"] for row in additions if row["region"] == region]
        if members:
            zones.append((zone_name, members))

    existing_zone_count = 0
    for index in range(ZONE_COUNT):
        start = ZONE_BASE + index * ZONE_SIZE
        name = original[start + 4:start + 20].rstrip(b"\xff\x00 ")
        if not name:
            break
        existing_zone_count = index + 1
    if existing_zone_count + len(zones) > ZONE_COUNT:
        raise RuntimeError("RT-4D zone bank is full")

    zone_rows = []
    for offset, (name, members) in enumerate(zones):
        zone_number = existing_zone_count + offset + 1
        start = ZONE_BASE + (zone_number - 1) * ZONE_SIZE
        result[start:start + ZONE_SIZE] = make_zone_record(name, members)
        zone_rows.append({
            "zone": zone_number,
            "name": name,
            "member_count": len(members),
            "first_channel": members[0],
            "last_channel": members[-1],
        })

    # List RX/TX pairs reused by multiple newly added repeaters. These are
    # intentional separate repeaters by location/tone, not silent dedupes.
    new_pair_groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in additions:
        new_pair_groups[frequency_pair(row)].append(row)
    shared = []
    for pair, rows in new_pair_groups.items():
        if len(rows) < 2:
            continue
        for row in rows:
            shared.append({
                "rx_mhz": f"{pair[0] / 100000:.5f}",
                "tx_mhz": f"{pair[1] / 100000:.5f}",
                "channel": row["channel"],
                "alias": row["alias"],
                "callsign": row["callsign"],
                "location": row["location"],
                "access": row["access_crosscheck"],
                "region": row["region"],
                "handling": "Kept separately: shared RF pair, different listed repeater/location",
            })

    output_path = args.output_dir / "RT4D_FULL_WI_NATIVE_ADDITIVE_CPS_CHECK_REQUIRED.ddmr"
    output_path.write_bytes(result)

    allowed = set()
    for row in additions:
        start = CHANNEL_BASE + (row["channel"] - 1) * CHANNEL_SIZE
        allowed.update(range(start, start + CHANNEL_SIZE))
    for row in zone_rows:
        start = ZONE_BASE + (row["zone"] - 1) * ZONE_SIZE
        allowed.update(range(start, start + ZONE_SIZE))
    diffs = [i for i, (before, after) in enumerate(zip(original, result)) if before != after]
    unexpected = [i for i in diffs if i not in allowed]
    if unexpected:
        raise RuntimeError(f"Unexpected changes outside new channels/zones: {unexpected[:20]}")
    for item in occupied:
        start = CHANNEL_BASE + (item["channel"] - 1) * CHANNEL_SIZE
        if result[start:start + CHANNEL_SIZE] != original[start:start + CHANNEL_SIZE]:
            raise RuntimeError(f"Existing channel {item['channel']} changed")
    for zone_number in range(1, existing_zone_count + 1):
        start = ZONE_BASE + (zone_number - 1) * ZONE_SIZE
        if result[start:start + ZONE_SIZE] != original[start:start + ZONE_SIZE]:
            raise RuntimeError(f"Existing zone {zone_number} changed")
    for row in additions:
        start = CHANNEL_BASE + (row["channel"] - 1) * CHANNEL_SIZE
        record = bytes(result[start:start + CHANNEL_SIZE])
        if decode_alias(record) != row["alias"]:
            raise RuntimeError(f"Alias validation failed at channel {row['channel']}")
        if (struct.unpack_from("<I", record, 5)[0], struct.unpack_from("<I", record, 9)[0]) != frequency_pair(row):
            raise RuntimeError(f"Frequency validation failed at channel {row['channel']}")
        if struct.unpack_from("<H", record, 15)[0] != encode_ctcss(row["access_crosscheck"]):
            raise RuntimeError(f"TX tone validation failed at channel {row['channel']}")

    addition_rows = [{
        "channel": row["channel"],
        "alias": row["alias"],
        "callsign": row["callsign"],
        "location": row["location"],
        "county": row["county_normalized"],
        "region": row["region"],
        "rx_mhz": row["rx_mhz"],
        "tx_mhz": row["tx_mhz"],
        "tx_ctcss": row["access_crosscheck"],
        "distance_miles": row["approx_distance_miles"],
        "source": row["crosscheck_source"],
    } for row in additions]
    write_csv(args.output_dir / "rt4d_new_channels_added.csv", addition_rows)
    write_csv(args.output_dir / "rt4d_skipped_existing_rx_tx_pairs.csv", skipped)
    write_csv(args.output_dir / "rt4d_shared_frequency_review.csv", shared, [
        "rx_mhz", "tx_mhz", "channel", "alias", "callsign", "location",
        "access", "region", "handling",
    ])
    write_csv(args.output_dir / "rt4d_new_zones_added.csv", zone_rows)

    validation = {
        "source_file": str(args.native),
        "source_sha256": sha256_bytes(original),
        "output_file": str(output_path),
        "output_sha256": sha256_bytes(bytes(result)),
        "file_size_bytes": len(result),
        "existing_channels_preserved": len(occupied),
        "existing_zones_preserved": existing_zone_count,
        "verified_analog_candidates": len(candidates),
        "skipped_existing_rx_tx_pairs": len(skipped),
        "new_channels_added": len(additions),
        "first_new_channel": additions[0]["channel"] if additions else "",
        "last_new_channel": additions[-1]["channel"] if additions else "",
        "new_zones_added": len(zone_rows),
        "new_zone_names": ", ".join(row["name"] for row in zone_rows),
        "shared_new_rf_rows_for_review": len(shared),
        "changed_bytes": len(diffs),
        "unexpected_changed_bytes": len(unexpected),
        "cps_validation": "REQUIRED before writing to radio",
        "excluded_input": "RT4D_V2_DMMR_FORMAT_TEST.csv was not read or used",
    }
    (args.output_dir / "rt4d_additive_validation.txt").write_text(
        "\n".join(f"{key}: {value}" for key, value in validation.items()) + "\n",
        encoding="utf-8",
    )
    print("\n".join(f"{key}: {value}" for key, value in validation.items()))


if __name__ == "__main__":
    main()

