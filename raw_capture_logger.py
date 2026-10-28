#!/usr/bin/env python3
"""Capture and validate the STM32 post-acquisition EMG binary dump.

Protocol v1
===========

The firmware first sends one CRLF-terminated ASCII line::

    EMG_CAPTURE_BEGIN,version=1,samples=8000,fs=4000,record_bytes=8,\
    scale=10000,first_sample=0,offset_millicounts=1861000,status=3,errors=0\r\n

Optional comma-separated ``key=value`` fields such as ``saturations=0`` may
follow and are preserved in the JSON metadata. The BEGIN line is followed by
exactly ``samples`` little-endian ``<Hhhh`` records (8 bytes each):

    uint16 adc_raw
    int16  hpf_q
    int16  bandpass_q
    int16  notch_q

The three signed values use ``scale`` integer counts per volt. Sample indices
are implicit: ``(first_sample + row_number) mod 2**32``. After the payload the
firmware sends::

    \r\nEMG_CAPTURE_END,crc32=89ABCDEF\r\n

The CRC is standard IEEE/zlib CRC-32 over the binary payload only and is
case-insensitive on input. At 8,000 samples the payload is 64,000 bytes, which
takes about 5.6 seconds to dump at 115,200 baud (8N1) after acquisition stops.

Live mode polls ``STATUS\n`` until the firmware reports idle/ready with no
errors, then sends ``CAPTURE\n``. Firmware responds ``CAPTURE_ARMED\r\n``,
acquires for two seconds, and then dumps the frame. Use ``--listen-only`` if
commands are being sent elsewhere.

Only live serial mode needs pyserial. Simulation and self-test modes use the
Python standard library only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping, Sequence


BEGIN_TAG = "EMG_CAPTURE_BEGIN"
END_TAG = "EMG_CAPTURE_END"
STATUS_TAG = "EMG_CAPTURE_STATUS"
BEGIN_PREFIX = BEGIN_TAG.encode("ascii")
RECORD_STRUCT = struct.Struct("<Hhhh")
RECORD_FIELDS = ("adc_raw", "hpf_q", "bandpass_q", "notch_q")
REQUIRED_BEGIN_KEYS = (
    "version",
    "samples",
    "fs",
    "record_bytes",
    "scale",
    "first_sample",
    "offset_millicounts",
    "status",
    "errors",
)
DEFAULT_SAMPLE_COUNT = 8000
DEFAULT_SAMPLE_RATE_HZ = 4000
DEFAULT_SCALE = 10000
DEFAULT_VREF_MV = 3300
DEFAULT_ADC_MAX = 4095
DEFAULT_EXPECTED_STATUS = 3
MAX_SAMPLE_COUNT = 1_000_000
MAX_ASCII_LINE_BYTES = 2048
MAX_PREAMBLE_BYTES = 16 * 1024
CRC_HEX_RE = re.compile(r"^[0-9A-Fa-f]{8}$")

MetadataValue = int | str
Record = tuple[int, int, int, int]


class CaptureError(RuntimeError):
    """Base class for protocol and transport failures."""


class CaptureTimeout(CaptureError):
    """A receive deadline expired; protocol_bytes holds recoverable bytes."""

    def __init__(
        self,
        message: str,
        *,
        protocol_bytes: bytes = b"",
        preamble_lines: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.protocol_bytes = protocol_bytes
        self.preamble_lines = list(preamble_lines)


@dataclass(frozen=True)
class ReceivedWire:
    protocol_bytes: bytes
    preamble_lines: list[str]


@dataclass(frozen=True)
class DecodedCapture:
    begin_line: bytes
    begin: dict[str, MetadataValue]
    payload: bytes
    separator: bytes
    end_line: bytes
    end: dict[str, MetadataValue]
    records: list[Record]
    report: dict[str, Any]


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def output_paths(stem: str | os.PathLike[str]) -> dict[str, Path]:
    base = os.fspath(stem)
    return {
        "binary": Path(base + ".bin"),
        "csv": Path(base + ".csv"),
        "metadata": Path(base + ".json"),
    }


def check_output_paths(paths: Mapping[str, Path], overwrite: bool) -> None:
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise CaptureError(
            "Output file(s) already exist. Use another --output-stem or "
            "--overwrite:\n  " + "\n  ".join(existing)
        )
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)


def _parse_metadata_line(line: bytes, expected_tag: str) -> dict[str, MetadataValue]:
    if not line.endswith(b"\n"):
        raise CaptureError(f"{expected_tag} line is not newline-terminated")
    if len(line) > MAX_ASCII_LINE_BYTES:
        raise CaptureError(f"{expected_tag} line is too long")
    try:
        text = line.rstrip(b"\r\n").decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise CaptureError(f"{expected_tag} line is not ASCII") from exc
    tokens = text.split(",")
    if not tokens or tokens[0] != expected_tag:
        raise CaptureError(f"Expected {expected_tag!r}, received {text[:80]!r}")

    values: dict[str, MetadataValue] = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise CaptureError(f"Malformed {expected_tag} token: {token!r}")
        key, raw_value = token.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key or not raw_value:
            raise CaptureError(f"Malformed {expected_tag} token: {token!r}")
        if key in values:
            raise CaptureError(f"Duplicate {expected_tag} key: {key!r}")
        try:
            values[key] = int(raw_value, 10)
        except ValueError:
            values[key] = raw_value
    return values


def _require_int(values: Mapping[str, MetadataValue], key: str) -> int:
    if key not in values:
        raise CaptureError(f"BEGIN line is missing required field {key!r}")
    value = values[key]
    if not isinstance(value, int):
        raise CaptureError(f"BEGIN field {key!r} must be a base-10 integer")
    return value


def parse_begin_line(line: bytes) -> dict[str, MetadataValue]:
    values = _parse_metadata_line(line, BEGIN_TAG)
    missing = [key for key in REQUIRED_BEGIN_KEYS if key not in values]
    if missing:
        raise CaptureError("BEGIN line missing field(s): " + ", ".join(missing))

    version = _require_int(values, "version")
    samples = _require_int(values, "samples")
    fs_hz = _require_int(values, "fs")
    record_bytes = _require_int(values, "record_bytes")
    scale = _require_int(values, "scale")
    first_sample = _require_int(values, "first_sample")
    offset_millicounts = _require_int(values, "offset_millicounts")
    status = _require_int(values, "status")
    errors = _require_int(values, "errors")

    if version != 1:
        raise CaptureError(f"Unsupported protocol version={version}; expected 1")
    if not 1 <= samples <= MAX_SAMPLE_COUNT:
        raise CaptureError("BEGIN samples value is outside the allowed range")
    if not 1 <= fs_hz <= 1_000_000:
        raise CaptureError("BEGIN fs value is outside the allowed range")
    if record_bytes != RECORD_STRUCT.size:
        raise CaptureError(
            f"Unsupported record_bytes={record_bytes}; expected {RECORD_STRUCT.size}"
        )
    if not 1 <= scale <= 1_000_000_000:
        raise CaptureError("BEGIN scale value is outside the allowed range")
    if not 0 <= first_sample <= 0xFFFFFFFF:
        raise CaptureError("BEGIN first_sample is outside uint32 range")
    if not 0 <= offset_millicounts <= 0xFFFFFFFF:
        raise CaptureError("BEGIN offset_millicounts is outside uint32 range")
    if not 0 <= status <= 0xFFFFFFFF:
        raise CaptureError("BEGIN status is outside uint32 range")
    if not 0 <= errors <= 0xFFFFFFFF:
        raise CaptureError("BEGIN errors is outside uint32 range")
    if "saturations" in values:
        saturations = _require_int(values, "saturations")
        if not 0 <= saturations <= 0xFFFFFFFF:
            raise CaptureError("BEGIN saturations is outside uint32 range")
    return values


def parse_end_line(line: bytes) -> dict[str, MetadataValue]:
    values = _parse_metadata_line(line, END_TAG)
    if "crc32" not in values:
        raise CaptureError("END line is missing required field 'crc32'")
    raw_crc = str(values["crc32"])
    # A digit-only 8-character CRC would otherwise have been converted to int.
    text = line.rstrip(b"\r\n").decode("ascii")
    for token in text.split(",")[1:]:
        key, raw_value = token.split("=", 1)
        if key.strip() == "crc32":
            raw_crc = raw_value.strip()
            break
    if not CRC_HEX_RE.fullmatch(raw_crc):
        raise CaptureError("END crc32 must contain exactly 8 hexadecimal digits")
    values["crc32"] = raw_crc.upper()
    return values


def build_begin_line(
    *,
    samples: int = DEFAULT_SAMPLE_COUNT,
    fs_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    scale: int = DEFAULT_SCALE,
    first_sample: int = 0,
    offset_millicounts: int = 1_861_000,
    status: int = DEFAULT_EXPECTED_STATUS,
    errors: int = 0,
    optional: Mapping[str, MetadataValue] | None = None,
) -> bytes:
    fields: list[tuple[str, MetadataValue]] = [
        ("version", 1),
        ("samples", samples),
        ("fs", fs_hz),
        ("record_bytes", RECORD_STRUCT.size),
        ("scale", scale),
        ("first_sample", first_sample),
        ("offset_millicounts", offset_millicounts),
        ("status", status),
        ("errors", errors),
    ]
    if optional:
        existing = {key for key, _ in fields}
        for key, value in optional.items():
            if key in existing or "," in key or "=" in key:
                raise ValueError(f"Invalid or duplicate optional BEGIN key: {key!r}")
            if isinstance(value, str) and any(char in value for char in ",\r\n"):
                raise ValueError(f"Invalid optional BEGIN value for {key!r}")
            fields.append((key, value))
            existing.add(key)
    body = BEGIN_TAG + "".join(f",{key}={value}" for key, value in fields)
    return (body + "\r\n").encode("ascii")


def build_end_line(payload: bytes) -> bytes:
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return f"{END_TAG},crc32={crc:08X}\r\n".encode("ascii")


def _read_one_byte(stream: BinaryIO, deadline: float) -> bytes:
    while time.monotonic() < deadline:
        data = stream.read(1)
        if data:
            return data
    return b""


def _read_ascii_line(
    stream: BinaryIO,
    deadline: float,
    *,
    partial_prefix: bytes,
    preamble_lines: Sequence[str],
    label: str,
) -> bytes:
    line = bytearray()
    while True:
        byte = _read_one_byte(stream, deadline)
        if not byte:
            raise CaptureTimeout(
                f"Timeout while waiting for {label}",
                protocol_bytes=partial_prefix + bytes(line),
                preamble_lines=preamble_lines,
            )
        line.extend(byte)
        if len(line) > MAX_ASCII_LINE_BYTES:
            raise CaptureError(f"Incoming {label} exceeded {MAX_ASCII_LINE_BYTES} bytes")
        if byte == b"\n":
            return bytes(line)


def _read_exact(
    stream: BinaryIO,
    size: int,
    deadline: float,
    *,
    partial_prefix: bytes,
    preamble_lines: Sequence[str],
    label: str,
) -> bytes:
    data = bytearray()
    while len(data) < size:
        if time.monotonic() >= deadline:
            raise CaptureTimeout(
                f"Timeout receiving {label}: got {len(data)} of {size} bytes",
                protocol_bytes=partial_prefix + bytes(data),
                preamble_lines=preamble_lines,
            )
        chunk = stream.read(min(4096, size - len(data)))
        if chunk:
            data.extend(chunk)
    return bytes(data)


def receive_wire(stream: BinaryIO, timeout_s: float) -> ReceivedWire:
    """Scan ASCII preamble, then receive exact BEGIN/payload/END bytes."""
    if timeout_s <= 0:
        raise CaptureError("Timeout must be greater than zero")
    deadline = time.monotonic() + timeout_s
    preamble_lines: list[str] = []
    preamble_bytes = 0
    while True:
        line = _read_ascii_line(
            stream,
            deadline,
            partial_prefix=b"",
            preamble_lines=preamble_lines,
            label="BEGIN line",
        )
        if line.startswith(BEGIN_PREFIX + b","):
            begin = parse_begin_line(line)
            break
        preamble_bytes += len(line)
        if preamble_bytes > MAX_PREAMBLE_BYTES:
            raise CaptureError("Too much ASCII preamble arrived before BEGIN")
        preamble_lines.append(line.rstrip(b"\r\n").decode("utf-8", errors="replace"))

    samples = _require_int(begin, "samples")
    record_bytes = _require_int(begin, "record_bytes")
    payload_size = samples * record_bytes
    payload = _read_exact(
        stream,
        payload_size,
        deadline,
        partial_prefix=line,
        preamble_lines=preamble_lines,
        label="binary payload",
    )
    separator = _read_exact(
        stream,
        2,
        deadline,
        partial_prefix=line + payload,
        preamble_lines=preamble_lines,
        label="CRLF before END",
    )
    if separator != b"\r\n":
        raise CaptureError(
            "Payload was not followed by the required CRLF before END "
            f"(received {separator!r})"
        )
    end_line = _read_ascii_line(
        stream,
        deadline,
        partial_prefix=line + payload + separator,
        preamble_lines=preamble_lines,
        label="END line",
    )
    parse_end_line(end_line)
    return ReceivedWire(line + payload + separator + end_line, preamble_lines)


def _integer_statistics(records: Sequence[Record]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for field_position, field_name in enumerate(RECORD_FIELDS):
        values = [record[field_position] for record in records]
        stats[field_name] = {
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": (sum(values) / len(values)) if values else None,
        }
    return stats


def decode_wire(
    protocol_bytes: bytes,
    *,
    expected_samples: int = DEFAULT_SAMPLE_COUNT,
    expected_status: int = DEFAULT_EXPECTED_STATUS,
    adc_max: int = DEFAULT_ADC_MAX,
) -> DecodedCapture:
    begin_end = protocol_bytes.find(b"\n")
    if begin_end < 0:
        raise CaptureError("Protocol binary contains no complete BEGIN line")
    begin_line = protocol_bytes[: begin_end + 1]
    begin = parse_begin_line(begin_line)
    samples = _require_int(begin, "samples")
    record_bytes = _require_int(begin, "record_bytes")
    payload_start = len(begin_line)
    payload_end = payload_start + samples * record_bytes
    if len(protocol_bytes) < payload_end + 2:
        raise CaptureError(
            f"Protocol binary is truncated: {len(protocol_bytes)} total bytes"
        )
    payload = protocol_bytes[payload_start:payload_end]
    separator = protocol_bytes[payload_end : payload_end + 2]
    if separator != b"\r\n":
        raise CaptureError("Protocol binary has no CRLF separator before END")
    end_line = protocol_bytes[payload_end + 2 :]
    if not end_line or not end_line.endswith(b"\n"):
        raise CaptureError("Protocol binary contains no complete END line")
    if end_line.count(b"\n") != 1:
        raise CaptureError("Unexpected bytes follow the END line")
    end = parse_end_line(end_line)
    records = list(RECORD_STRUCT.iter_unpack(payload))
    computed_crc = zlib.crc32(payload) & 0xFFFFFFFF
    transmitted_crc = int(str(end["crc32"]), 16)
    offset_millicounts = _require_int(begin, "offset_millicounts")
    saturations = begin.get("saturations", 0)
    saturations_numeric = saturations if isinstance(saturations, int) else None

    validations = {
        "sample_count_matches_cli": samples == expected_samples,
        "record_count_matches_begin": len(records) == samples,
        "crc32_valid": transmitted_crc == computed_crc,
        "status_matches_expected": _require_int(begin, "status") == expected_status,
        "errors_zero": _require_int(begin, "errors") == 0,
        "adc_values_in_range": all(record[0] <= adc_max for record in records),
        "offset_in_adc_range": offset_millicounts <= adc_max * 1000,
        "saturations_zero_if_reported": (
            saturations_numeric in (None, 0)
        ),
    }
    adc_out_of_range = sum(1 for record in records if record[0] > adc_max)
    report: dict[str, Any] = {
        "capture_valid": all(validations.values()),
        "validations": validations,
        "expected_samples_cli": expected_samples,
        "expected_status_cli": expected_status,
        "record_count": len(records),
        "payload_bytes": len(payload),
        "crc32_transmitted": f"{transmitted_crc:08X}",
        "crc32_computed": f"{computed_crc:08X}",
        "adc_out_of_range_count": adc_out_of_range,
        "implicit_first_sample_index": _require_int(begin, "first_sample"),
        "implicit_last_sample_index": (
            _require_int(begin, "first_sample") + max(0, len(records) - 1)
        )
        & 0xFFFFFFFF,
        "integer_field_statistics": _integer_statistics(records),
    }
    return DecodedCapture(
        begin_line=begin_line,
        begin=begin,
        payload=payload,
        separator=separator,
        end_line=end_line,
        end=end,
        records=records,
        report=report,
    )


def _write_csv(
    path: Path,
    capture: DecodedCapture,
    *,
    vref_mv: int,
    adc_max: int,
) -> None:
    fs_hz = _require_int(capture.begin, "fs")
    scale = _require_int(capture.begin, "scale")
    first_sample = _require_int(capture.begin, "first_sample")
    offset_counts = _require_int(capture.begin, "offset_millicounts") / 1000.0
    vref_v = vref_mv / 1000.0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "sample_index",
                "time_s",
                "adc_raw",
                "adc_voltage_v",
                "adc_centered_v",
                "hpf_q",
                "bandpass_q",
                "notch_q",
                "hpf_v",
                "bandpass_v",
                "notch_v",
            )
        )
        for row, (adc_raw, hpf_q, bandpass_q, notch_q) in enumerate(capture.records):
            sample_index = (first_sample + row) & 0xFFFFFFFF
            writer.writerow(
                (
                    sample_index,
                    f"{row / fs_hz:.9f}",
                    adc_raw,
                    f"{adc_raw * vref_v / adc_max:.9f}",
                    f"{(adc_raw - offset_counts) * vref_v / adc_max:.9f}",
                    hpf_q,
                    bandpass_q,
                    notch_q,
                    f"{hpf_q / scale:.7f}",
                    f"{bandpass_q / scale:.7f}",
                    f"{notch_q / scale:.7f}",
                )
            )


def write_outputs(
    paths: Mapping[str, Path],
    protocol_bytes: bytes,
    capture: DecodedCapture,
    *,
    source: str,
    port: str | None,
    baudrate: int | None,
    timeout_s: float | None,
    vref_mv: int,
    adc_max: int,
    preamble_lines: Sequence[str] = (),
) -> None:
    paths["binary"].write_bytes(protocol_bytes)
    _write_csv(paths["csv"], capture, vref_mv=vref_mv, adc_max=adc_max)
    armed_seen = any(line.strip() == "CAPTURE_ARMED" for line in preamble_lines)
    metadata = {
        "created_utc": utc_now_text(),
        "protocol": "EMG_CAPTURE_V1",
        "source": source,
        "serial": {
            "port": port,
            "baudrate": baudrate,
            "format": "8N1",
            "timeout_s": timeout_s,
        },
        "wire_format": {
            "begin_ascii": capture.begin_line.decode("ascii").rstrip("\r\n"),
            "record_struct_python": RECORD_STRUCT.format,
            "record_size_bytes": RECORD_STRUCT.size,
            "record_fields_transmitted": list(RECORD_FIELDS),
            "sample_index": "implicit first_sample + zero-based row, uint32 wrap",
            "payload_endianness": "little",
            "crc": "CRC-32/IEEE over payload only; 8 hex digits in END line",
            "scale_counts_per_volt": _require_int(capture.begin, "scale"),
            "adc_conversion_assumptions": {
                "vref_mv": vref_mv,
                "adc_max": adc_max,
            },
        },
        "begin_metadata": capture.begin,
        "end_metadata": capture.end,
        "validation": capture.report,
        "discarded_ascii_preamble_lines": list(preamble_lines),
        "capture_armed_response_seen": armed_seen,
        "files": {
            "binary": str(paths["binary"].resolve()),
            "csv": str(paths["csv"].resolve()),
            "metadata": str(paths["metadata"].resolve()),
        },
        "sha256_protocol_binary": hashlib.sha256(protocol_bytes).hexdigest(),
    }
    paths["metadata"].write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_failure_outputs(
    paths: Mapping[str, Path],
    *,
    protocol_bytes: bytes,
    error: BaseException,
    source: str,
    port: str | None,
    baudrate: int | None,
    timeout_s: float | None,
    preamble_lines: Sequence[str] = (),
) -> None:
    paths["binary"].write_bytes(protocol_bytes)
    metadata = {
        "created_utc": utc_now_text(),
        "protocol": "EMG_CAPTURE_V1",
        "source": source,
        "capture_valid": False,
        "fatal_error": f"{type(error).__name__}: {error}",
        "serial": {
            "port": port,
            "baudrate": baudrate,
            "format": "8N1",
            "timeout_s": timeout_s,
        },
        "received_protocol_bytes": len(protocol_bytes),
        "discarded_ascii_preamble_lines": list(preamble_lines),
        "files": {
            "binary": str(paths["binary"].resolve()),
            "csv": None,
            "metadata": str(paths["metadata"].resolve()),
        },
        "sha256_protocol_binary": hashlib.sha256(protocol_bytes).hexdigest(),
    }
    paths["metadata"].write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_simulated_records(
    samples: int = DEFAULT_SAMPLE_COUNT,
    fs_hz: int = DEFAULT_SAMPLE_RATE_HZ,
) -> list[Record]:
    records: list[Record] = []
    adc_counts_per_volt = DEFAULT_ADC_MAX / (DEFAULT_VREF_MV / 1000.0)
    for row in range(samples):
        t = row / fs_hz
        muscle_v = 0.030 * math.sin(2.0 * math.pi * 90.0 * t)
        muscle_v += 0.012 * math.sin(2.0 * math.pi * 180.0 * t + 0.4)
        mains_v = 0.018 * math.sin(2.0 * math.pi * 50.0 * t + 0.2)
        drift_v = 0.040 * math.sin(2.0 * math.pi * 2.0 * t)
        adc_raw = round((1.5 + muscle_v + mains_v + drift_v) * adc_counts_per_volt)
        adc_raw = min(DEFAULT_ADC_MAX, max(0, adc_raw))
        stage_volts = (
            muscle_v + mains_v,
            0.96 * muscle_v + mains_v,
            0.96 * muscle_v + 0.03 * mains_v,
        )
        q = [round(value * DEFAULT_SCALE) for value in stage_volts]
        q = [min(32767, max(-32768, value)) for value in q]
        records.append((adc_raw, q[0], q[1], q[2]))
    return records


def encode_wire(
    records: Iterable[Record],
    *,
    fs_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    scale: int = DEFAULT_SCALE,
    first_sample: int = 0,
    offset_millicounts: int = 1_861_000,
    status: int = DEFAULT_EXPECTED_STATUS,
    errors: int = 0,
    optional: Mapping[str, MetadataValue] | None = None,
) -> bytes:
    record_list = list(records)
    begin = build_begin_line(
        samples=len(record_list),
        fs_hz=fs_hz,
        scale=scale,
        first_sample=first_sample,
        offset_millicounts=offset_millicounts,
        status=status,
        errors=errors,
        optional=optional,
    )
    payload = bytearray(len(record_list) * RECORD_STRUCT.size)
    offset = 0
    for record in record_list:
        RECORD_STRUCT.pack_into(payload, offset, *record)
        offset += RECORD_STRUCT.size
    payload_bytes = bytes(payload)
    return begin + payload_bytes + b"\r\n" + build_end_line(payload_bytes)


def print_summary(capture: DecodedCapture, paths: Mapping[str, Path]) -> None:
    report = capture.report
    validity = "VALID" if report["capture_valid"] else "INVALID"
    duration_s = _require_int(capture.begin, "samples") / _require_int(
        capture.begin, "fs"
    )
    print(f"Capture {validity}: {report['record_count']} records ({duration_s:.3f} s)")
    print(
        f"CRC32: transmitted {report['crc32_transmitted']}, "
        f"computed {report['crc32_computed']}"
    )
    print(
        f"Status={capture.begin['status']}, errors={capture.begin['errors']}, "
        f"ADC out of range={report['adc_out_of_range_count']}"
    )
    print(f"Saved binary:   {paths['binary']}")
    print(f"Saved CSV:      {paths['csv']}")
    print(f"Saved metadata: {paths['metadata']}")
    if not report["capture_valid"]:
        failed = [name for name, passed in report["validations"].items() if not passed]
        print("Failed validation(s): " + ", ".join(failed), file=sys.stderr)


def run_simulation(args: argparse.Namespace) -> int:
    paths = output_paths(args.output_stem)
    check_output_paths(paths, args.overwrite)
    records = make_simulated_records(args.expected_samples, DEFAULT_SAMPLE_RATE_HZ)
    wire = encode_wire(
        records,
        optional={"saturations": 0, "source": "SIMULATOR"},
    )
    if args.simulate_corrupt_crc:
        payload_start = wire.index(b"\n") + 1
        damaged = bytearray(wire)
        damaged[payload_start + 21] ^= 0x01
        wire = bytes(damaged)
    capture = decode_wire(
        wire,
        expected_samples=args.expected_samples,
        expected_status=args.expected_status,
        adc_max=args.adc_max,
    )
    write_outputs(
        paths,
        wire,
        capture,
        source="simulation",
        port=None,
        baudrate=None,
        timeout_s=None,
        vref_mv=args.vref_mv,
        adc_max=args.adc_max,
    )
    print_summary(capture, paths)
    return 0 if capture.report["capture_valid"] else 2


def _open_serial(port: str, baudrate: int):
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CaptureError(
            "Live capture requires pyserial. Install it with: "
            "python -m pip install pyserial"
        ) from exc
    device = serial.Serial()
    device.port = port
    device.baudrate = baudrate
    device.bytesize = serial.EIGHTBITS
    device.parity = serial.PARITY_NONE
    device.stopbits = serial.STOPBITS_ONE
    device.timeout = 0.20
    device.write_timeout = 2.0
    device.xonxoff = False
    device.rtscts = False
    device.dsrdtr = False
    device.dtr = False
    device.rts = False
    device.open()
    return device


def parse_status_line(line: bytes) -> dict[str, MetadataValue]:
    """Parse and validate one EMG_CAPTURE_STATUS response line."""
    values = _parse_metadata_line(line, STATUS_TAG)
    for key in ("state", "status", "errors", "ready", "samples"):
        value = values.get(key)
        if not isinstance(value, int):
            raise CaptureError(f"STATUS field {key!r} must be an integer")
    return values


def write_command_slow(device: Any, command: bytes, byte_delay_s: float = 0.003) -> None:
    """Send an ASCII command slowly enough for the firmware's polled UART RX."""
    for value in command:
        device.write(bytes((value,)))
        device.flush()
        time.sleep(byte_delay_s)


def wait_until_ready(
    device: Any,
    *,
    timeout_s: float,
    expected_status: int,
) -> dict[str, MetadataValue]:
    """Poll firmware STATUS and return only when CAPTURE can be accepted."""
    deadline = time.monotonic() + timeout_s
    last_status: dict[str, MetadataValue] | None = None

    while time.monotonic() < deadline:
        write_command_slow(device, b"STATUS\n")
        response_deadline = min(deadline, time.monotonic() + 0.75)

        while time.monotonic() < response_deadline:
            line = device.readline()
            if not line:
                continue
            if not line.startswith((STATUS_TAG + ",").encode("ascii")):
                continue
            last_status = parse_status_line(line)
            if (
                last_status["state"] == 0
                and last_status["status"] == expected_status
                and last_status["errors"] == 0
                and last_status["ready"] == 1
            ):
                return last_status
            break

        time.sleep(0.10)

    if last_status is None:
        detail = "no valid EMG_CAPTURE_STATUS response received"
    else:
        detail = ", ".join(
            f"{key}={last_status[key]}"
            for key in ("state", "status", "errors", "ready", "samples")
        )
    raise CaptureTimeout(
        f"Firmware did not become capture-ready within {timeout_s:.1f} s ({detail})"
    )


def run_live(args: argparse.Namespace) -> int:
    if not args.port:
        raise CaptureError("Live mode requires --port, for example --port COM5")
    paths = output_paths(args.output_stem)
    check_output_paths(paths, args.overwrite)
    protocol_bytes = b""
    preamble_lines: list[str] = []
    try:
        with _open_serial(args.port, args.baudrate) as device:
            if not args.listen_only:
                device.reset_input_buffer()
                # Terminate any command fragment left in the firmware parser by
                # a previous serial disconnect. An empty line is harmless when
                # the parser is already clear.
                write_command_slow(device, b"\n")
                time.sleep(0.10)
                ready_status = wait_until_ready(
                    device,
                    timeout_s=args.ready_timeout,
                    expected_status=args.expected_status,
                )
                print(
                    "STM32 ready: "
                    f"status={ready_status['status']}, "
                    f"errors={ready_status['errors']}, "
                    f"ready={ready_status['ready']}."
                )
                # Give the blocking STATUS response handler time to return to
                # its receive loop before sending the next command.
                time.sleep(0.10)
                write_command_slow(device, b"CAPTURE\n")
                print("CAPTURE sent; keep the muscle condition steady.")
            print(
                f"Listening on {args.port} at {args.baudrate} baud "
                f"(total timeout {args.timeout:.1f} s)..."
            )
            received = receive_wire(device, args.timeout)
            protocol_bytes = received.protocol_bytes
            preamble_lines = received.preamble_lines
        capture = decode_wire(
            protocol_bytes,
            expected_samples=args.expected_samples,
            expected_status=args.expected_status,
            adc_max=args.adc_max,
        )
    except CaptureTimeout as exc:
        protocol_bytes = exc.protocol_bytes
        preamble_lines = exc.preamble_lines
        write_failure_outputs(
            paths,
            protocol_bytes=protocol_bytes,
            error=exc,
            source="serial",
            port=args.port,
            baudrate=args.baudrate,
            timeout_s=args.timeout,
            preamble_lines=preamble_lines,
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Partial binary: {paths['binary']}", file=sys.stderr)
        print(f"Failure metadata: {paths['metadata']}", file=sys.stderr)
        return 2
    except (CaptureError, OSError) as exc:
        write_failure_outputs(
            paths,
            protocol_bytes=protocol_bytes,
            error=exc,
            source="serial",
            port=args.port,
            baudrate=args.baudrate,
            timeout_s=args.timeout,
            preamble_lines=preamble_lines,
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Failure metadata: {paths['metadata']}", file=sys.stderr)
        return 2

    write_outputs(
        paths,
        protocol_bytes,
        capture,
        source="serial",
        port=args.port,
        baudrate=args.baudrate,
        timeout_s=args.timeout,
        vref_mv=args.vref_mv,
        adc_max=args.adc_max,
        preamble_lines=preamble_lines,
    )
    if not args.listen_only and not any(
        line.strip() == "CAPTURE_ARMED" for line in preamble_lines
    ):
        print(
            "WARNING: CAPTURE_ARMED was not seen before BEGIN; inspect JSON preamble.",
            file=sys.stderr,
        )
    print_summary(capture, paths)
    return 0 if capture.report["capture_valid"] else 2


def run_self_test() -> int:
    records = make_simulated_records()
    wire = encode_wire(
        records,
        first_sample=0xFFFFFF00,
        optional={"saturations": 0, "board": "BLACKPILL"},
    )
    capture = decode_wire(wire)
    if not capture.report["capture_valid"]:
        raise AssertionError("Valid 8000-record fixture failed validation")
    if len(capture.records) != DEFAULT_SAMPLE_COUNT:
        raise AssertionError("Decoded record count is incorrect")
    if capture.begin.get("board") != "BLACKPILL":
        raise AssertionError("Unknown BEGIN metadata was not preserved")
    if capture.report["implicit_last_sample_index"] != (
        0xFFFFFF00 + DEFAULT_SAMPLE_COUNT - 1
    ) & 0xFFFFFFFF:
        raise AssertionError("Implicit uint32 sample-index reconstruction failed")

    with tempfile.TemporaryDirectory(prefix="emg_capture_selftest_") as temp_dir:
        paths = output_paths(Path(temp_dir) / "simulated_capture")
        check_output_paths(paths, overwrite=False)
        write_outputs(
            paths,
            wire,
            capture,
            source="self-test",
            port=None,
            baudrate=None,
            timeout_s=None,
            vref_mv=DEFAULT_VREF_MV,
            adc_max=DEFAULT_ADC_MAX,
            preamble_lines=["CAPTURE_ARMED"],
        )
        for path in paths.values():
            if not path.is_file() or path.stat().st_size == 0:
                raise AssertionError(f"Missing/empty output: {path}")
        with paths["csv"].open("r", newline="", encoding="utf-8") as handle:
            if sum(1 for _ in handle) != DEFAULT_SAMPLE_COUNT + 1:
                raise AssertionError("CSV does not contain header plus all samples")
        metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
        if not metadata["validation"]["capture_valid"]:
            raise AssertionError("JSON did not preserve valid capture result")
        if not metadata["capture_armed_response_seen"]:
            raise AssertionError("CAPTURE_ARMED preamble was not recorded")

    payload_start = wire.index(b"\n") + 1
    damaged = bytearray(wire)
    damaged[payload_start + 17] ^= 0x20
    damaged_capture = decode_wire(bytes(damaged))
    if damaged_capture.report["validations"]["crc32_valid"]:
        raise AssertionError("Payload corruption was not caught by CRC32")
    if damaged_capture.report["capture_valid"]:
        raise AssertionError("CRC-corrupted capture was marked valid")

    crc_text = capture.report["crc32_transmitted"]
    lower_wire = wire.replace(crc_text.encode("ascii"), crc_text.lower().encode("ascii"))
    if not decode_wire(lower_wire).report["validations"]["crc32_valid"]:
        raise AssertionError("Lowercase CRC hex was not accepted")

    streamed = receive_wire(
        io.BytesIO(b"EMG_STATUS,status=3,errors=0\r\nCAPTURE_ARMED\r\n" + wire),
        timeout_s=2.0,
    )
    if streamed.protocol_bytes != wire:
        raise AssertionError("Stream receiver did not preserve exact protocol bytes")
    if streamed.preamble_lines != [
        "EMG_STATUS,status=3,errors=0",
        "CAPTURE_ARMED",
    ]:
        raise AssertionError("Stream receiver did not preserve ASCII preamble")

    parsed_status = parse_status_line(
        b"EMG_CAPTURE_STATUS,version=1,state=0,status=3,errors=0,ready=1,samples=0\r\n"
    )
    if parsed_status["ready"] != 1 or parsed_status["errors"] != 0:
        raise AssertionError("STATUS parser did not preserve readiness fields")

    print(
        "SELF-TEST PASSED: 8000-record <Hhhh> frame, implicit indices, outputs, "
        "CRC corruption/case, status parsing, unknown metadata, and serial preamble scan."
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Send CAPTURE to STM32, receive a post-capture binary EMG frame, "
            "and save exact .bin, decoded .csv, and validation .json files."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--simulate",
        action="store_true",
        help="Generate a deterministic 8000-record frame without serial hardware",
    )
    mode.add_argument(
        "--self-test",
        action="store_true",
        help="Run parser/protocol/output tests in a temporary directory",
    )
    parser.add_argument("--port", help="CH340 serial port in live mode, e.g. COM5")
    parser.add_argument(
        "--output-stem",
        help="Path without extension; writes STEM.bin, STEM.csv, and STEM.json",
    )
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Total seconds for response, acquisition, payload, and END (default: 45)",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for idle/ready status before CAPTURE (default: 15)",
    )
    parser.add_argument(
        "--expected-samples",
        type=int,
        default=DEFAULT_SAMPLE_COUNT,
        help="Required BEGIN sample count (default: 8000)",
    )
    parser.add_argument(
        "--expected-status",
        type=int,
        default=DEFAULT_EXPECTED_STATUS,
        help="Required BEGIN status value (default: 3)",
    )
    parser.add_argument(
        "--vref-mv",
        type=int,
        default=DEFAULT_VREF_MV,
        help="ADC reference used only for CSV voltage conversion (default: 3300)",
    )
    parser.add_argument(
        "--adc-max",
        type=int,
        default=DEFAULT_ADC_MAX,
        help="ADC full-scale code used for validation/conversion (default: 4095)",
    )
    parser.add_argument(
        "--listen-only",
        action="store_true",
        help="Do not flush or send STATUS/CAPTURE; only wait for a dump",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output files with the same stem",
    )
    parser.add_argument("--simulate-corrupt-crc", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if not args.output_stem:
        parser.error("--output-stem is required unless --self-test is used")
    if not 1 <= args.expected_samples <= MAX_SAMPLE_COUNT:
        parser.error(f"--expected-samples must be 1..{MAX_SAMPLE_COUNT}")
    if not 0 <= args.expected_status <= 0xFFFFFFFF:
        parser.error("--expected-status must fit uint32")
    if args.baudrate <= 0 or args.timeout <= 0 or args.ready_timeout <= 0:
        parser.error("--baudrate, --timeout, and --ready-timeout must be greater than zero")
    if not 1 <= args.vref_mv <= 100_000:
        parser.error("--vref-mv is outside the supported range")
    if not 1 <= args.adc_max <= 0xFFFF:
        parser.error("--adc-max is outside uint16 range")
    try:
        if args.simulate:
            return run_simulation(args)
        return run_live(args)
    except (CaptureError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
