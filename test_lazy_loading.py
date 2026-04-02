#!/usr/bin/env python3
"""
Test script for IfcOpenShell lazy attribute loading.

Compares normal vs lazy mode on IFC files, measuring memory usage and load time.
Lazy mode defers parsing entity attributes until first access, reducing memory
and speeding up initial load for large files.

HOW TO RUN
----------
Prerequisites:
  - Built tt-ifcopenshell with Python bindings (see NOTES_ON_BUILD.md)

Setup with uv (recommended):
    cd tt-ifcopenshell
    uv venv .venv --python 3.11          # Must match the cpython version the .so was built for
    uv pip install psutil numpy typing_extensions

Run:
    PYTHONPATH=src/ifcopenshell-python .venv/bin/python test_lazy_loading.py [IFC_FILE]

Alternative setup with conda:
    source ~/miniforge3/bin/activate ifcopenshell-dev
    PYTHONPATH=src/ifcopenshell-python python test_lazy_loading.py [IFC_FILE]

Examples:
    # Default: runs against the 63MB PLP Envelope file
    PYTHONPATH=src/ifcopenshell-python .venv/bin/python test_lazy_loading.py

    # Test with the 900MB MEP file
    PYTHONPATH=src/ifcopenshell-python .venv/bin/python test_lazy_loading.py \
        /home/ahmada/TestLargeIfcFiles/tt-speckleifc/samples/LARGEIFC_PLP_HolbornViaduct_MEP.ifc

    # Lazy-only mode (skip normal baseline, useful for very large files)
    PYTHONPATH=src/ifcopenshell-python .venv/bin/python test_lazy_loading.py --lazy-only /path/to/file.ifc

    # Load file into memory first (simulates blob storage / BytesIO scenario)
    PYTHONPATH=src/ifcopenshell-python .venv/bin/python test_lazy_loading.py --from-memory /path/to/file.ifc

    # Combine flags: lazy-only + from-memory
    PYTHONPATH=src/ifcopenshell-python .venv/bin/python test_lazy_loading.py --lazy-only --from-memory /path/to/file.ifc

IMPORTANT: Python version must match the compiled extension.
  The .so is named _ifcopenshell_wrapper.cpython-311-*.so — so you need Python 3.11.
  If you rebuild for a different Python version, update the venv accordingly.

Output:
    Prints a summary table comparing memory and timing between modes.
"""

import argparse
import gc
import os
import sys
import time

import psutil


def mem_mb() -> float:
    """Current process RSS in MB."""
    return psutil.Process().memory_info().rss / (1024 * 1024)


def file_size_mb(path: str) -> float:
    return os.path.getsize(path) / (1024 * 1024)


def run_test(ifc_path: str, lazy: bool, from_memory: bool = False) -> dict:
    """
    Open an IFC file in normal or lazy mode and measure memory + timing.

    If from_memory=True, reads the entire file into a Python bytes object first,
    then loads via ifcopenshell.file.from_string(). This simulates the real-world
    scenario of loading IFC data from blob storage / BytesIO.

    Returns a dict with all metrics.
    """
    import ifcopenshell

    gc.collect()
    baseline = mem_mb()

    source_label = "memory" if from_memory else "disk"
    ifc_data: bytes = b""

    if from_memory:
        # Read entire file into Python memory first
        print(f"  Reading file into memory...", end=" ", flush=True)
        t_read = time.time()
        with open(ifc_path, "rb") as fh:
            ifc_data = fh.read()
        t_read = time.time() - t_read
        mem_after_read = mem_mb()
        print(f"{len(ifc_data) / (1024*1024):.1f} MB in {t_read:.2f}s (mem: +{mem_after_read - baseline:.1f} MB)")
    else:
        mem_after_read = baseline

    # Load into ifcopenshell
    t0 = time.time()
    if from_memory:
        # Decode bytes to str (IFC-SPF is ASCII text)
        f = ifcopenshell.file.from_string(ifc_data.decode("latin-1"), lazy=lazy)
    else:
        f = ifcopenshell.open(ifc_path, lazy=lazy)
    t_load = time.time() - t0
    mem_after_load = mem_mb()

    # Free the Python bytes buffer now that ifcopenshell has its own copy
    if from_memory:
        del ifc_data
        gc.collect()
        mem_after_free = mem_mb()
    else:
        mem_after_free = mem_after_load

    # Entity count (iteration only, no attribute access)
    t1 = time.time()
    entity_count = len(list(f))
    t_count = time.time() - t1

    # Query by type + access attributes (triggers materialization in lazy mode)
    t2 = time.time()
    walls = f.by_type("IfcWall")
    wall_count = len(walls)
    first_wall_guid = walls[0].GlobalId if walls else "N/A"
    first_wall_name = walls[0].Name if walls else "N/A"
    t_walls = time.time() - t2

    mem_after_walls = mem_mb()

    # Query more types
    t3 = time.time()
    slabs = f.by_type("IfcSlab")
    columns = f.by_type("IfcColumn")
    beams = f.by_type("IfcBeam")
    spaces = f.by_type("IfcSpace")
    t_more = time.time() - t3

    mem_after_queries = mem_mb()

    # Schema info
    schema = f.schema

    # Cleanup
    del f, walls, slabs, columns, beams, spaces
    gc.collect()
    mem_after_del = mem_mb()

    return {
        "mode": ("LAZY" if lazy else "NORMAL") + (" (from memory)" if from_memory else " (from disk)"),
        "schema": schema,
        "source": source_label,
        "baseline": baseline,
        "mem_after_read": mem_after_read,
        "mem_delta_read": mem_after_read - baseline,
        "mem_after_load": mem_after_load,
        "mem_delta_load": mem_after_load - baseline,
        "mem_after_free": mem_after_free,
        "mem_delta_free": mem_after_free - baseline,
        "mem_after_walls": mem_after_walls,
        "mem_delta_walls": mem_after_walls - baseline,
        "mem_after_queries": mem_after_queries,
        "mem_delta_queries": mem_after_queries - baseline,
        "mem_after_del": mem_after_del,
        "t_load": t_load,
        "t_count": t_count,
        "t_walls": t_walls,
        "t_more": t_more,
        "entity_count": entity_count,
        "wall_count": wall_count,
        "first_wall_guid": first_wall_guid,
        "first_wall_name": first_wall_name,
    }


def print_result(r: dict, file_mb: float):
    mode = r["mode"]
    print(f"=== {mode} ===")
    print(f"  Schema:          {r['schema']}")
    if r["source"] == "memory":
        print(f"  Mem for bytes:   +{r['mem_delta_read']:.1f} MB (file read into Python)")
    print(f"  Load time:       {r['t_load']:.2f}s")
    print(f"  Mem after load:  {r['mem_after_load']:.1f} MB (+{r['mem_delta_load']:.1f} MB)")
    print(f"  Mem/file ratio:  {r['mem_delta_load'] / file_mb:.1f}x file size")
    if r["source"] == "memory":
        print(f"  Mem after free:  {r['mem_after_free']:.1f} MB (+{r['mem_delta_free']:.1f} MB) (after del bytes)")
    print(f"  Entities:        {r['entity_count']:,}")
    print(f"  Count time:      {r['t_count']:.2f}s")
    print(f"  Walls:           {r['wall_count']} (query: {r['t_walls']:.3f}s)")
    print(f"  First wall:      {r['first_wall_guid']} / {r['first_wall_name']}")
    print(f"  Mem after walls: {r['mem_after_walls']:.1f} MB (+{r['mem_delta_walls']:.1f} MB)")
    print(f"  Mem after all:   {r['mem_after_queries']:.1f} MB (+{r['mem_delta_queries']:.1f} MB)")
    print(f"  Mem after del:   {r['mem_after_del']:.1f} MB")
    print()


def print_comparison(normal: dict, lazy: dict, file_mb: float):
    print("=== COMPARISON ===")
    print(f"  {'Metric':<25} {'Normal':>12} {'Lazy':>12} {'Savings':>12}")
    print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*12}")

    n_load = normal["mem_delta_load"]
    l_load = lazy["mem_delta_load"]
    pct_load = (1 - l_load / n_load) * 100 if n_load > 0 else 0
    print(f"  {'Mem at load (MB)':<25} {n_load:>12.1f} {l_load:>12.1f} {pct_load:>11.1f}%")

    n_walls = normal["mem_delta_walls"]
    l_walls = lazy["mem_delta_walls"]
    pct_walls = (1 - l_walls / n_walls) * 100 if n_walls > 0 else 0
    print(f"  {'Mem after walls (MB)':<25} {n_walls:>12.1f} {l_walls:>12.1f} {pct_walls:>11.1f}%")

    n_time = normal["t_load"]
    l_time = lazy["t_load"]
    pct_time = (1 - l_time / n_time) * 100 if n_time > 0 else 0
    print(f"  {'Load time (s)':<25} {n_time:>12.2f} {l_time:>12.2f} {pct_time:>11.1f}%")

    print(f"  {'Entities':<25} {normal['entity_count']:>12,} {lazy['entity_count']:>12,}")
    print(f"  {'Walls':<25} {normal['wall_count']:>12} {lazy['wall_count']:>12}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Test IfcOpenShell lazy loading performance")
    parser.add_argument(
        "ifc_file",
        nargs="?",
        default="/home/ahmada/TestLargeIfcFiles/tt-speckleifc/samples/LARGEIFC_PLP_HolbornViaduct_Envelope.ifc",
        help="Path to IFC file (default: PLP Envelope 63MB)",
    )
    parser.add_argument(
        "--lazy-only",
        action="store_true",
        help="Only run lazy mode (skip normal baseline). Useful for very large files.",
    )
    parser.add_argument(
        "--from-memory",
        action="store_true",
        help="Read file into Python bytes first, then load via from_string(). "
             "Simulates loading IFC from blob storage / BytesIO.",
    )
    args = parser.parse_args()

    ifc_path = os.path.abspath(args.ifc_file)
    if not os.path.exists(ifc_path):
        print(f"ERROR: File not found: {ifc_path}")
        sys.exit(1)

    file_mb = file_size_mb(ifc_path)
    print(f"File: {ifc_path}")
    print(f"Size: {file_mb:.1f} MB")
    if args.from_memory:
        print(f"Source: in-memory (file read into Python bytes first)")
    else:
        print(f"Source: disk")
    print()

    normal_result = None
    if not args.lazy_only:
        normal_result = run_test(ifc_path, lazy=False, from_memory=args.from_memory)
        print_result(normal_result, file_mb)

    lazy_result = run_test(ifc_path, lazy=True, from_memory=args.from_memory)
    print_result(lazy_result, file_mb)

    if normal_result:
        print_comparison(normal_result, lazy_result, file_mb)
    else:
        print("(Skipped normal mode comparison — run without --lazy-only to compare)")


if __name__ == "__main__":
    main()
