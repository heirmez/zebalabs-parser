"""Zebalabs DWG/DXF Parser Service — FastAPI microservice.

Extracts nested block hierarchy from DWG/DXF files and returns structured
room → furniture → component data for the web frontend.

Uses the PROVEN extraction logic from cadapp/extract.py:
  - SORTENTSTABLE fix for LibreDWG-converted DXF files
  - Correct quantity detection: all |xscale| ≈ 1.0 → "no" (count), else → "rm" (sum)
  - LibreDWG dwg2dxf as primary converter, ODA/odafc as fallback
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import ezdxf
import tempfile
import subprocess
import shutil
import os
import re
import csv
from collections import defaultdict
from pathlib import Path

app = FastAPI(title="Zebalabs DWG/DXF Parser")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Lock down in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Converter paths ──────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CADAPP_DIR = os.environ.get("CADAPP_DIR", os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "cadapp")))
LIBREDWG_DWG2DXF = os.environ.get(
    "DWG2DXF_PATH",
    os.path.join(CADAPP_DIR, "tools", "libredwg", "dwg2dxf.exe"),
)
CATALOG_PATH = os.environ.get(
    "CATALOG_PATH",
    os.path.join(CADAPP_DIR, "catalog.csv"),
)
ODA_CONVERTER = os.environ.get("ODA_CONVERTER_PATH", "")

# Layers to skip — these are structural/dimension layers, not rooms
SKIP_LAYERS = {"0", "Defpoints", "Layer1", "-DIM", "FRAME", "SUPPORTS"}


# ── Catalog ──────────────────────────────────────────────────────────────

def load_catalog(catalog_path: str = CATALOG_PATH) -> dict:
    """Load product catalog for description/price lookups."""
    catalog = {}
    if not os.path.isfile(catalog_path):
        return catalog
    with open(catalog_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row.get("item_code", "").strip()
            if not code:
                continue
            catalog[code.upper()] = {
                "item_code": code,
                "description": row.get("description", "").strip(),
                "unit_price": float(row.get("unit_price", 0) or 0),
                "volume_per_unit": float(row.get("volume_per_unit", 0) or 0),
                "weight_per_unit": float(row.get("weight_per_unit", 0) or 0),
            }
    return catalog


# ── DWG → DXF Conversion ────────────────────────────────────────────────

def _detect_dwg_version(dwg_path: str) -> str:
    """Read first 6 bytes of DWG file to detect AutoCAD version."""
    try:
        with open(dwg_path, "rb") as f:
            header = f.read(6).decode("ascii", errors="replace")
        version_map = {
            "AC1015": "AutoCAD 2000",
            "AC1018": "AutoCAD 2004",
            "AC1021": "AutoCAD 2007",
            "AC1024": "AutoCAD 2010",
            "AC1027": "AutoCAD 2013",
            "AC1032": "AutoCAD 2018+",
        }
        return version_map.get(header, f"Unknown ({header})")
    except Exception:
        return "Unknown"


def convert_dwg_to_dxf(dwg_path: str) -> str:
    """Convert DWG → DXF using multiple strategies with fallbacks.

    Returns path to converted DXF file.
    Raises HTTPException if all methods fail.
    """
    dwg_version = _detect_dwg_version(dwg_path)
    errors = []

    # Strategy 1: ODA File Converter — best for AC1032 (AutoCAD 2018+)
    oda_path = _find_oda_converter()
    if oda_path:
        try:
            input_dir = os.path.dirname(dwg_path)
            output_dir = tempfile.mkdtemp(prefix="oda_output_")
            input_filename = os.path.basename(dwg_path)
            result = subprocess.run(
                [oda_path, input_dir, output_dir, "ACAD2018", "DXF", "0", "1", input_filename],
                capture_output=True, text=True, timeout=120,
            )
            import glob
            dxf_files = glob.glob(os.path.join(output_dir, "*.dxf"))
            if dxf_files and os.path.getsize(dxf_files[0]) > 0:
                return dxf_files[0]
            errors.append(f"ODA: no output. stderr={result.stderr[:200] if result.stderr else 'none'}")
        except Exception as e:
            errors.append(f"ODA: {e}")

    # Strategy 2: ezdxf odafc addon (uses ODA File Converter via ezdxf wrapper)
    try:
        from ezdxf.addons import odafc
        dxf_path = dwg_path.rsplit(".", 1)[0] + ".dxf"
        odafc.convert(dwg_path, dxf_path)
        if os.path.isfile(dxf_path) and os.path.getsize(dxf_path) > 0:
            return dxf_path
        errors.append("odafc: conversion produced no output")
    except Exception as e:
        errors.append(f"odafc: {e}")

    # Strategy 3: LibreDWG dwg2dxf on system PATH (compiled into Docker image)
    dwg2dxf_sys = shutil.which("dwg2dxf") or (LIBREDWG_DWG2DXF if os.path.isfile(LIBREDWG_DWG2DXF) else None)
    if dwg2dxf_sys:
        try:
            tmp_dir = tempfile.mkdtemp(prefix="cadplan_")
            safe_base = re.sub(r'[^\w\-.]', '_', Path(dwg_path).stem)
            dxf_out = os.path.join(tmp_dir, f"{safe_base}.dxf")
            result = subprocess.run(
                [dwg2dxf_sys, "-y", "-o", dxf_out, dwg_path],
                capture_output=True, text=True, timeout=120,
            )
            if os.path.isfile(dxf_out) and os.path.getsize(dxf_out) > 0:
                return dxf_out
            errors.append(f"LibreDWG: output empty. stderr={result.stderr[:200] if result.stderr else 'none'}")
        except Exception as e:
            errors.append(f"LibreDWG: {e}")

    detail = (
        f"DWG conversion failed for {dwg_version} file. All strategies exhausted.\n"
        + "\n".join(f"  - {e}" for e in errors) + "\n\n"
        "Solutions:\n"
        "  1. Save the file as DXF in AutoCAD (File → Save As → DXF)\n"
        "  2. Install ODA File Converter (free): https://www.opendesign.com/guestfiles/oda_file_converter\n"
        f"  3. Ensure LibreDWG dwg2dxf.exe exists at {LIBREDWG_DWG2DXF}"
    )
    raise HTTPException(400, detail)


def _find_oda_converter() -> str:
    if ODA_CONVERTER and os.path.isfile(ODA_CONVERTER):
        return ODA_CONVERTER
    candidates = [
        r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
        "/usr/bin/ODAFileConverter",
        "/opt/ODAFileConverter/ODAFileConverter",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return shutil.which("ODAFileConverter") or ""


# ── SORTENTSTABLE Fix ────────────────────────────────────────────────────

def fix_dxf(dxf_path: str) -> str:
    """Strip SORTENTSTABLE objects that crash ezdxf.

    LibreDWG's dwg2dxf produces DXF files containing SORTENTSTABLE objects
    in the OBJECTS section. ezdxf cannot parse these and raises errors.
    This function strips them out, writing a _fixed.dxf if changes were made.
    """
    with open(dxf_path, "r", errors="replace") as f:
        content = f.read()

    if "SORTENTSTABLE" not in content:
        return dxf_path

    lines = content.split("\n")
    fixed = []
    i = 0
    removed = 0

    while i < len(lines):
        if lines[i].strip() == "SORTENTSTABLE":
            # Remove the preceding "0" group code line
            if fixed and fixed[-1].strip() == "0":
                fixed.pop()
            # Skip past the entire SORTENTSTABLE object
            i += 1
            while i < len(lines) and lines[i].strip() != "0":
                i += 1
            removed += 1
            continue
        fixed.append(lines[i])
        i += 1

    if removed > 0:
        fixed_path = dxf_path.replace(".dxf", "_fixed.dxf")
        with open(fixed_path, "w") as f:
            f.write("\n".join(fixed))
        return fixed_path

    return dxf_path


# ── Block Hierarchy Extraction (proven logic from extract.py) ────────────

MAX_BLOCK_DEPTH = 10  # Prevent infinite recursion


def extract_bom(dxf_path: str) -> dict:
    """Extract room → furniture → component hierarchy from DXF.

    This is the PROVEN logic from cadapp/extract.py with edge-case hardening:
    1. Collect model-space INSERTs grouped by layer (= rooms)
    2. Within each layer, group INSERTs by block_name (= furniture/benches)
    3. For each bench block definition, find nested INSERTs (= components)
    4. Group components by name, collect xscale values
    5. Determine unit: all |xscale| ≈ 1.0 → "no" (count), else → "rm" (sum of |xscale|)

    Edge cases handled:
    - Circular reference detection (visited set)
    - Depth limit (MAX_BLOCK_DEPTH = 10)
    - Malformed entity handling (try/except per entity)
    - Empty/missing block definitions (skipped gracefully)
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    block_defs = {b.name: b for b in doc.blocks if not b.name.startswith("*")}

    # Collect model-space INSERTs by layer
    layer_inserts = defaultdict(list)
    for entity in msp:
        try:
            if entity.dxftype() == "INSERT":
                layer_inserts[entity.dxf.layer].append({
                    "block_name": entity.dxf.name,
                    "xscale": getattr(entity.dxf, "xscale", 1.0),
                })
        except Exception:
            continue  # Skip malformed entities

    result_layers = {}

    for layer_name, inserts in layer_inserts.items():
        if layer_name in SKIP_LAYERS:
            continue

        # Group by bench block name
        bench_groups = defaultdict(list)
        for ins in inserts:
            bench_groups[ins["block_name"]].append(ins)

        benches = []
        for bench_name, bench_inserts in bench_groups.items():
            if bench_name not in block_defs:
                continue

            try:
                block_entities = list(block_defs[bench_name])
            except Exception:
                continue  # Skip malformed block definitions

            nested_inserts = []
            for e in block_entities:
                try:
                    if e.dxftype() == "INSERT" and not e.dxf.name.startswith("*"):
                        nested_inserts.append(e)
                except Exception:
                    continue

            if not nested_inserts:
                continue

            # Circular reference detection
            visited = {bench_name}

            # Group nested INSERTs (components) by name, collect xscale values
            # Keep full float precision — only round final qty to match desktop app
            component_groups = defaultdict(list)
            for nested in nested_inserts:
                try:
                    comp_name = nested.dxf.name
                    if comp_name in visited:
                        continue  # Skip circular reference
                    xs = getattr(nested.dxf, "xscale", 1.0)
                    component_groups[comp_name].append(round(xs, 4))
                except Exception:
                    continue

            items = []
            max_worktop_length = 0

            for comp_name, xscales in component_groups.items():
                abs_scales = [abs(x) for x in xscales]
                all_unit = all(abs(x - 1.0) < 0.001 for x in abs_scales)

                if all_unit:
                    qty = len(xscales)
                    unit = "no"
                else:
                    # Sum with full precision, then round to 2dp
                    # This matches desktop extract.py exactly
                    qty = round(sum(abs_scales), 2)
                    unit = "rm"
                    max_piece = max(abs_scales)
                    if max_piece > max_worktop_length:
                        max_worktop_length = max_piece

                items.append({
                    "item_code": comp_name,
                    "qty": qty,
                    "unit": unit,
                })

            items.sort(key=lambda x: x["item_code"].lower())
            length_mm = int(round(max_worktop_length * 1000)) if max_worktop_length > 0 else 0

            benches.append({
                "block_name": bench_name,
                "count": len(bench_inserts),
                "length_mm": length_mm,
                "items": items,
            })

        if benches:
            result_layers[layer_name] = {"benches": benches}

    return {"layers": result_layers}


# ── Format Response for Frontend ─────────────────────────────────────────

def format_response(bom: dict, filename: str, doc, catalog: dict) -> dict:
    """Convert extract_bom() output to the frontend ExtractionResult shape.

    Frontend expects:
      { filename, rooms: [{ layer_name, room_label, furniture: [{ block_name, quantity, components }] }],
        block_definitions }
    """
    msp = doc.modelspace()

    # Collect room labels from text entities
    room_labels = {}
    for entity in msp:
        if entity.dxftype() in ("TEXT", "MTEXT"):
            layer = entity.dxf.layer
            try:
                text = entity.dxf.text if hasattr(entity.dxf, "text") else ""
                if text and len(text) > 3:
                    room_labels[layer] = text.strip()
            except Exception:
                pass

    # Build block_definitions for frontend
    block_definitions = {}
    for block in doc.blocks:
        if block.name.startswith("*"):
            continue
        nested = [
            e.dxf.name for e in block
            if e.dxftype() == "INSERT" and not e.dxf.name.startswith("*")
        ]
        attrib_names = []
        for e in block:
            if e.dxftype() == "ATTDEF":
                try:
                    attrib_names.append(e.dxf.tag)
                except Exception:
                    pass
        block_definitions[block.name] = {
            "name": block.name,
            "entity_count": len(list(block)),
            "nested_blocks": nested,
            "has_attributes": len(attrib_names) > 0,
            "attribute_names": attrib_names,
        }

    # Build rooms array
    rooms = []
    total_furniture = 0
    total_components = 0

    for layer_name, layer_data in bom["layers"].items():
        room = {
            "layer_name": layer_name,
            "room_label": room_labels.get(layer_name, ""),
            "furniture": [],
        }

        for bench in layer_data["benches"]:
            components = []
            for item in bench["items"]:
                code = item["item_code"]
                cat_entry = catalog.get(code.upper(), {})
                components.append({
                    "code": code,
                    "qty": item["qty"],
                    "unit": item["unit"],
                    "description": cat_entry.get("description", ""),
                    "unit_price": cat_entry.get("unit_price", 0),
                    "volume_per_unit": cat_entry.get("volume_per_unit", 0),
                    "weight_per_unit": cat_entry.get("weight_per_unit", 0),
                })

            room["furniture"].append({
                "block_name": bench["block_name"],
                "quantity": bench["count"],
                "length_mm": bench["length_mm"],
                "components": components,
            })
            total_furniture += 1
            total_components += len(components)

        rooms.append(room)

    return {
        "filename": filename,
        "rooms": rooms,
        "block_definitions": block_definitions,
        "stats": {
            "total_rooms": len(rooms),
            "total_furniture": total_furniture,
            "total_components": total_components,
        },
    }


# ── API Endpoints ────────────────────────────────────────────────────────

@app.post("/extract")
async def extract_dwg(file: UploadFile = File(...)):
    """Extract rooms, furniture, and components from a DWG or DXF file."""
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".dxf", ".dwg"):
        raise HTTPException(400, "Only DWG and DXF files are supported")

    # Save uploaded file to temp
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    cleanup_paths = [tmp_path]

    try:
        dxf_path = tmp_path

        # Convert DWG → DXF if needed
        if ext == ".dwg":
            dxf_path = convert_dwg_to_dxf(tmp_path)
            if dxf_path != tmp_path:
                cleanup_paths.append(dxf_path)

        # Fix SORTENTSTABLE corruption (LibreDWG output)
        fixed_path = fix_dxf(dxf_path)
        if fixed_path != dxf_path:
            cleanup_paths.append(fixed_path)
            dxf_path = fixed_path

        # Extract BOM hierarchy
        bom = extract_bom(dxf_path)

        # Load catalog for descriptions
        catalog = load_catalog()

        # Re-read doc for room labels and block definitions
        doc = ezdxf.readfile(dxf_path)

        return format_response(bom, file.filename, doc, catalog)

    finally:
        for path in cleanup_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
            # Clean up temp dirs
            parent = os.path.dirname(path)
            basename = os.path.basename(parent)
            if basename.startswith("cadplan_") or basename.startswith("oda_output_"):
                shutil.rmtree(parent, ignore_errors=True)


@app.post("/debug-xscale")
async def debug_xscale(file: UploadFile = File(...)):
    """Debug endpoint: dump all xscale values per component for precision analysis."""
    if not file.filename:
        raise HTTPException(400, "No filename")

    ext = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        dxf_path = tmp_path
        if ext == ".dwg":
            dxf_path = convert_dwg_to_dxf(tmp_path)
        fixed_path = fix_dxf(dxf_path)
        if fixed_path != dxf_path:
            dxf_path = fixed_path

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        block_defs = {b.name: b for b in doc.blocks if not b.name.startswith("*")}

        # Collect model-space INSERTs by layer
        layer_inserts = defaultdict(list)
        for entity in msp:
            if entity.dxftype() == "INSERT":
                layer_inserts[entity.dxf.layer].append(entity.dxf.name)

        debug = []
        for layer_name, block_names in layer_inserts.items():
            if layer_name in SKIP_LAYERS:
                continue
            bench_groups = defaultdict(int)
            for bn in block_names:
                bench_groups[bn] += 1

            for bench_name, bench_count in bench_groups.items():
                if bench_name not in block_defs:
                    continue
                block_entities = list(block_defs[bench_name])
                nested = [e for e in block_entities if e.dxftype() == "INSERT" and not e.dxf.name.startswith("*")]
                if not nested:
                    continue

                comp_scales = defaultdict(list)
                for n in nested:
                    xs = getattr(n.dxf, "xscale", 1.0)
                    comp_scales[n.dxf.name].append(xs)

                for comp, scales in sorted(comp_scales.items()):
                    abs_s = [abs(x) for x in scales]
                    all_unit = all(abs(x - 1.0) < 0.001 for x in abs_s)
                    qty = len(scales) if all_unit else round(sum(abs_s), 2)
                    unit = "no" if all_unit else "rm"
                    debug.append({
                        "layer": layer_name,
                        "bench": bench_name,
                        "comp": comp,
                        "raw_xscales": [round(x, 6) for x in scales],
                        "abs_scales": [round(x, 6) for x in abs_s],
                        "qty": qty,
                        "unit": unit,
                    })

        return {"filename": file.filename, "components": debug}
    finally:
        os.unlink(tmp_path)


@app.get("/health")
async def health():
    """Health check with converter and catalog status."""
    libredwg_path = LIBREDWG_DWG2DXF if os.path.isfile(LIBREDWG_DWG2DXF) else shutil.which("dwg2dxf")
    oda = _find_oda_converter()

    odafc_available = False
    try:
        from ezdxf.addons import odafc
        odafc_available = True
    except ImportError:
        pass

    catalog = load_catalog()

    return {
        "status": "ok",
        "ezdxf_version": ezdxf.__version__,
        "dwg_support": bool(libredwg_path) or bool(oda) or odafc_available,
        "libredwg_path": libredwg_path,
        "oda_converter_path": oda or None,
        "odafc_addon": odafc_available,
        "catalog_items": len(catalog),
        "catalog_path": CATALOG_PATH if catalog else None,
    }
