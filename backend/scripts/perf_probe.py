"""Measures what running this application actually costs the machine.

Ingestion is CPU-bound and runs for minutes, so the question is not only "how
long" but "is the machine still usable while it happens". Both are measured
here against a running server, from outside it, the way a person would
experience them:

- **Responsiveness probe.** A fixed amount of arithmetic, timed repeatedly.
  Its duration is the honest signal - if the machine is saturated or swapping,
  identical work takes longer, and by how much is the answer.
- **Memory.** The server's working set and the system's free memory, because
  the worst failure here was swapping rather than slowness.
- **Latency.** Wall-clock for ingest and for each question.

    python scripts/perf_probe.py --file <path> [--questions questions.jsonl]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8001"


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def free_ram_gb() -> float:
    try:
        if sys.platform == "win32":
            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullAvailPhys / 1024**3
        import os

        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")) / 1024**3
    except Exception:
        return 0.0


def probe_ms() -> float:
    """Time a fixed unit of work. Longer means the machine is busier."""
    started = time.perf_counter()
    total = 0
    for i in range(400_000):
        total += i * i
    return (time.perf_counter() - started) * 1000


def sample(label: str, t0: float) -> dict:
    row = {
        "t": time.perf_counter() - t0,
        "free_gb": free_ram_gb(),
        "probe_ms": probe_ms(),
        "label": label,
    }
    print(f"  {row['t']:7.1f}s  free {row['free_gb']:5.2f}GB  probe {row['probe_ms']:6.1f}ms  {label}")
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--doc-name", default=None, help="match questions on this doc_name")
    args = parser.parse_args()

    client = httpx.Client(timeout=600)
    samples: list[dict] = []

    print(f"\n  {'=' * 66}\n  BASELINE (nothing running)\n  {'=' * 66}")
    t0 = time.perf_counter()
    baseline = [sample("idle", t0) for _ in range(3)]
    idle_probe = statistics.median(s["probe_ms"] for s in baseline)

    print(f"\n  {'=' * 66}\n  INGESTION\n  {'=' * 66}")
    started = time.perf_counter()
    with args.file.open("rb") as handle:
        response = client.post(f"{BASE}/filings", files={"file": (args.file.name, handle)})
    response.raise_for_status()
    filing_id = response.json()["id"]
    accepted = time.perf_counter() - started
    print(f"  upload accepted in {accepted:.2f}s -> {filing_id}")

    status, seen = "queued", {}
    while status not in ("ready", "failed"):
        row = sample(status, t0)
        samples.append(row)
        body = client.get(f"{BASE}/filings/{filing_id}").json()
        status = body["status"]
        seen.setdefault(status, time.perf_counter() - started)
        time.sleep(2)

    ingest_s = time.perf_counter() - started
    print(f"\n  ingest total : {ingest_s:.1f}s")
    print(f"  pages        : {body.get('num_pages')}   status: {status}")
    if status == "failed":
        print(f"  error        : {body.get('error')}")
        return
    for phase, at in seen.items():
        print(f"  reached {phase:<10} at {at:6.1f}s")

    during = [s["probe_ms"] for s in samples] or [idle_probe]
    print(f"\n  {'=' * 66}\n  RESPONSIVENESS DURING INGEST\n  {'=' * 66}")
    print(f"  idle probe          : {idle_probe:6.1f} ms")
    print(f"  median during ingest: {statistics.median(during):6.1f} ms"
          f"   ({statistics.median(during)/idle_probe:.2f}x idle)")
    print(f"  worst during ingest : {max(during):6.1f} ms"
          f"   ({max(during)/idle_probe:.2f}x idle)")
    print(f"  free RAM low-water  : {min(s['free_gb'] for s in samples):5.2f} GB")

    if not args.questions:
        return

    questions = [
        json.loads(line)
        for line in args.questions.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.doc_name:
        questions = [q for q in questions if q.get("doc_name") == args.doc_name]

    print(f"\n  {'=' * 66}\n  QUESTIONS ({len(questions)})\n  {'=' * 66}")
    results = []
    for i, q in enumerate(questions, 1):
        asked = time.perf_counter()
        try:
            answer = client.post(
                f"{BASE}/chat/ask",
                json={"filing_id": filing_id, "question": q["question"]},
                timeout=600,
            ).json()
        except Exception as exc:
            print(f"  [{i}] request failed: {exc}")
            continue
        latency = time.perf_counter() - asked
        expected_pages = sorted({e["evidence_page_num"] + 1 for e in q.get("evidence") or []})
        results.append(
            {
                "question": q["question"],
                "expected": q.get("answer"),
                "expected_pages": expected_pages,
                "found": answer.get("found"),
                "answer": answer.get("answer"),
                "page": answer.get("page"),
                "latency_s": latency,
            }
        )
        verdict = "answered" if answer.get("found") else "declined"
        print(f"\n  [{i}] {q['question'][:88]}")
        print(f"      {verdict} in {latency:.1f}s   cited page {answer.get('page')}"
              f"   (ground truth {expected_pages})")
        print(f"      got      : {(answer.get('answer') or answer.get('reason') or '')[:150]}")
        print(f"      expected : {str(q.get('answer'))[:150]}")

    if results:
        lat = [r["latency_s"] for r in results]
        answered = sum(1 for r in results if r["found"])
        page_hits = sum(
            1 for r in results if r["found"] and r["page"] in r["expected_pages"]
        )
        print(f"\n  {'=' * 66}\n  SUMMARY\n  {'=' * 66}")
        print(f"  answered            : {answered}/{len(results)}")
        print(f"  cited a ground-truth page: {page_hits}/{len(results)}")
        print(f"  latency median      : {statistics.median(lat):.1f}s")
        print(f"  latency min/max     : {min(lat):.1f}s / {max(lat):.1f}s")
        Path("perf_report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  detail written to   : perf_report.json")


if __name__ == "__main__":
    main()
