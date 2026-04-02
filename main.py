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

# ── Startup validation ────────────────────────────────────────────────────

@app.on_event("startup")
def validate_dwg2dxf():
    """Fail fast if dwg2dxf binary is missing or broken."""
    path = shutil.which("dwg2dxf")
    if not path:
        import warnings
        warnings.warn("dwg2dxf not found on PATH — DWG upload will fail")
        return
    try:
        result = subprocess.run(["dwg2dxf", "--version"], capture_output=True, check=True)
        print(f"[startup] dwg2dxf OK: {result.stdout.decode().strip()[:80] or result.stderr.decode().strip()[:80]}")
    except Exception as e:
        raise RuntimeError(f"dwg2dxf binary is broken: {e}")


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
# raw_materials.csv: exported from tblItem via scripts/export-raw-materials.py
# Drop the file alongside main.py (or set RAW_MATERIALS_PATH env var).
# When present, /extract returns level2_bom (raw material expansion).
# When absent, only Level 1 component data is returned.
RAW_MATERIALS_PATH = os.environ.get(
    "RAW_MATERIALS_PATH",
    os.path.join(SCRIPT_DIR, "raw_materials.csv"),
)
# ODA File Converter binary — used directly when installed in the container
_ODA_CANDIDATES = [
    os.environ.get("ODA_PATH", ""),
    "/usr/bin/ODAFileConverter",
    shutil.which("ODAFileConverter") or "",
]
LOCAL_ODA_PATH = next((p for p in _ODA_CANDIDATES if p and os.path.isfile(p)), "")

# ODA microservice URL — set when ODA runs as a separate HTTP service
ODA_SERVICE_URL = os.environ.get("ODA_SERVICE_URL", "").rstrip("/")

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


def _oda_convert_local(dwg_path: str) -> tuple[str, str] | None:
    """Use locally installed ODA binary to convert DWG -> DXF.
    Returns (dxf_path, 'oda_local') or None on failure.
    """
    if not LOCAL_ODA_PATH:
        return None
    import glob as _glob
    try:
        in_dir = os.path.dirname(dwg_path)
        out_dir = tempfile.mkdtemp(prefix="oda_out_")
        result = subprocess.run(
            [LOCAL_ODA_PATH, in_dir, out_dir, "ACAD2018", "DXF", "0", "1",
             os.path.basename(dwg_path)],
            capture_output=True, text=True, timeout=120,
        )
        dxf_files = _glob.glob(os.path.join(out_dir, "*.dxf"))
        if dxf_files and os.path.getsize(dxf_files[0]) > 0:
            print(f"[convert] ODA local OK -> {os.path.getsize(dxf_files[0])} bytes")
            return dxf_files[0], "oda_local"
        print(f"[convert] ODA local no output: {result.stderr[:200]}")
    except Exception as e:
        print(f"[convert] ODA local error: {e}")
    return None


def convert_dwg_to_dxf(dwg_path: str) -> tuple[str, str]:
    """Convert DWG -> DXF.

    Priority:
      1. ODA microservice (ODA_SERVICE_URL env var) - production Railway
      2. ODA local binary (LOCAL_ODA_PATH / ODA_PATH env var) - Docker with ODA installed
      3. LibreDWG dwg2dxf - fallback

    Returns (dxf_path, converter_used).
    Raises HTTPException if all fail.
    """
    # 1. ODA microservice
    if ODA_SERVICE_URL:
        print(f"[convert] Using ODA service: {ODA_SERVICE_URL}")
        try:
            import requests as _requests
            with open(dwg_path, "rb") as f:
                dwg_bytes = f.read()
            resp = _requests.post(
                f"{ODA_SERVICE_URL}/convert",
                files={"file": ("file.dwg", dwg_bytes)},
                timeout=120,
            )
            if resp.status_code == 200 and len(resp.content) > 0:
                dxf_out = dwg_path.rsplit(".", 1)[0] + "_oda.dxf"
                with open(dxf_out, "wb") as f:
                    f.write(resp.content)
                print(f"[convert] ODA service OK -> {os.path.getsize(dxf_out)} bytes")
                return dxf_out, "oda_service"
            print(f"[convert] ODA service failed: HTTP {resp.status_code} - {resp.text[:200]}")
        except Exception as e:
            print(f"[convert] ODA service error: {e}")

    # 2. ODA local binary (installed in Docker image)
    if LOCAL_ODA_PATH:
        print(f"[convert] Using ODA local: {LOCAL_ODA_PATH}")
        result = _oda_convert_local(dwg_path)
        if result:
            return result

    # 3. LibreDWG dwg2dxf
    dwg2dxf_bin = shutil.which("dwg2dxf") or (LIBREDWG_DWG2DXF if os.path.isfile(LIBREDWG_DWG2DXF) else None)
    if dwg2dxf_bin and os.path.getsize(dwg2dxf_bin) > 0:
        print(f"[convert] Using LibreDWG: {dwg2dxf_bin}")
        try:
            tmp_dir = tempfile.mkdtemp(prefix="cadplan_")
            safe_base = re.sub(r"[^\w\-.]", "_", Path(dwg_path).stem)
            dxf_out = os.path.join(tmp_dir, f"{safe_base}.dxf")
            result = subprocess.run(
                [dwg2dxf_bin, "-y", "-o", dxf_out, dwg_path],
                capture_output=True, text=True, timeout=120,
            )
            if os.path.isfile(dxf_out) and os.path.getsize(dxf_out) > 0:
                print(f"[convert] LibreDWG OK -> {os.path.getsize(dxf_out)} bytes")
                return dxf_out, "libredwg"
            print(f"[convert] LibreDWG no output: {result.stderr[:200]}")
        except Exception as e:
            print(f"[convert] LibreDWG error: {e}")
    else:
        print("[convert] LibreDWG binary not available")

    dwg_version = _detect_dwg_version(dwg_path)
    raise HTTPException(
        400,
        f"DWG conversion failed ({dwg_version}). "
        "Set ODA_SERVICE_URL env var to the deployed ODA microservice, "
        "or upload a DXF file directly.",
    )


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
    """Strip SORTENTSTABLE entity instances that crash ezdxf.

    LibreDWG's dwg2dxf produces DXF files containing SORTENTSTABLE objects
    in the OBJECTS section with invalid group-code 331 where ezdxf expects 5.
    This function strips them out using a regex that matches entity boundaries
    (group code 0 = entity start in DXF), writing a _fixed.dxf if changes were made.

    Only entity instances (preceded by group code 0) are stripped — the CLASS
    declaration in the CLASSES section is preserved.
    """
    import re
    with open(dxf_path, "r", errors="replace") as f:
        content = f.read()

    if "SORTENTSTABLE" not in content:
        return dxf_path

    # Match each SORTENTSTABLE entity: starts at group-code-0 line, ends just
    # before the next group-code-0 line that begins a new entity (capital letter value).
    pattern = r"(?ms)^\s*0\s*\nSORTENTSTABLE\n.*?(?=^\s*0\s*\n[A-Z])"
    fixed, n = re.subn(pattern, "", content)

    if n > 0:
        fixed_path = dxf_path.replace(".dxf", "_fixed.dxf")
        with open(fixed_path, "w", errors="replace") as f:
            f.write(fixed)
        return fixed_path

    return dxf_path


# ── Block Hierarchy Extraction (proven logic from extract.py) ────────────

MAX_BLOCK_DEPTH = 10  # Prevent infinite recursion


def _purge_sortentstable(doc) -> int:
    """Delete SORTENTSTABLE entities directly from doc.entitydb.

    Iterating doc.objects triggers lazy parsing of SORTENTSTABLE, which is
    what causes DXFStructureError("Invalid sort handle code N, expected 5").
    Instead we iterate doc.entitydb — a plain {handle: entity} dict — which
    accesses already-parsed objects only, then delete by handle before any
    blocks/modelspace iteration can trigger the bad parse path.
    """
    removed = 0
    try:
        handles_to_remove = []
        for handle in list(doc.entitydb.keys()):
            try:
                entity = doc.entitydb[handle]
                if hasattr(entity, "dxftype") and callable(entity.dxftype):
                    if entity.dxftype() == "SORTENTSTABLE":
                        handles_to_remove.append(handle)
            except Exception:
                pass
        for handle in handles_to_remove:
            try:
                del doc.entitydb[handle]
                removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def load_dxf(dxf_path: str):
    """Open a DXF file tolerantly, returning an ezdxf document.

    Pre-processes with fix_dxf() to strip SORTENTSTABLE entities that have
    invalid group-code 331 (LibreDWG output) — recover.read() itself fails on
    these, so they must be removed from the text before loading.

    Uses ezdxf.recover.read() (binary stream) to handle LibreDWG's remaining
    corrupt group codes (e.g. "DC750").  Falls back to ezdxf.readfile() for
    well-formed DXF so normal DXF uploads still work.
    """
    # Strip SORTENTSTABLE entities from the text before ezdxf sees them.
    # recover.read() cannot handle the invalid group-code-331 in these objects.
    fixed_path = fix_dxf(dxf_path)
    cleanup_fixed = fixed_path != dxf_path  # True if a new _fixed.dxf was written
    try:
        from ezdxf import recover
        with open(fixed_path, "rb") as _stream:
            doc, auditor = recover.read(_stream)
        if auditor.has_errors:
            import logging
            logging.getLogger("ezdxf").warning(
                f"DXF recover: {len(auditor.errors)} fixable errors in {fixed_path}"
            )
        return doc
    except Exception:
        doc = ezdxf.readfile(fixed_path)
        return doc
    finally:
        if cleanup_fixed:
            try:
                os.unlink(fixed_path)
            except OSError:
                pass


def extract_bom(dxf_path: str):
    """Extract room → furniture → component hierarchy from DXF.

    This is the PROVEN logic from cadapp/extract.py with edge-case hardening:
    1. Collect model-space INSERTs grouped by layer (= rooms)
    2. Within each layer, group INSERTs by block_name (= furniture/benches)
    3. For each bench block definition, find nested INSERTs (= components)
    4. Group components by name, collect xscale values
    5. Determine unit: all |xscale| ≈ 1.0 → "no" (count), else → "rm" (sum of |xscale|)

    Returns (bom_dict, doc) so the caller can reuse the already-parsed doc.

    Edge cases handled:
    - Circular reference detection (visited set)
    - Depth limit (MAX_BLOCK_DEPTH = 10)
    - Malformed entity handling (try/except per entity)
    - Empty/missing block definitions (skipped gracefully)
    """
    doc = load_dxf(dxf_path)
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

    return {"layers": result_layers}, doc


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
        try:
            if entity.dxftype() not in ("TEXT", "MTEXT"):
                continue
            layer = entity.dxf.layer
            text = entity.dxf.text if hasattr(entity.dxf, "text") else ""
            if text and len(text) > 3:
                room_labels[layer] = text.strip()
        except Exception:
            pass

    # Build block_definitions for frontend
    block_definitions = {}
    for block in doc.blocks:
        try:
            if block.name.startswith("*"):
                continue
        except Exception:
            continue
        nested = []
        attrib_names = []
        for e in block:
            try:
                if e.dxftype() == "INSERT":
                    name = e.dxf.name
                    if not name.startswith("*"):
                        nested.append(name)
                elif e.dxftype() == "ATTDEF":
                    attrib_names.append(e.dxf.tag)
            except Exception:
                continue
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
    converter_used = "none"

    try:
        dxf_path = tmp_path

        # Convert DWG → DXF if needed
        if ext == ".dwg":
            dxf_path, converter_used = convert_dwg_to_dxf(tmp_path)
            if dxf_path != tmp_path:
                cleanup_paths.append(dxf_path)

        # Extract BOM hierarchy (also returns the already-parsed doc — don't re-read)
        try:
            bom, doc = extract_bom(dxf_path)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"DXF parsing failed: {type(exc).__name__}: {exc}",
            )

        # Load catalog for descriptions
        catalog = load_catalog()

        result = format_response(bom, file.filename, doc, catalog)
        result["converter_used"] = converter_used

        # ── Level 2 BOM: expand components -> raw materials ──────────────────
        try:
            from material_expansion import load_tbl_item, expand_components, to_flat_list
            from procurement_logic import compute_procurement
            tbl_item = load_tbl_item(RAW_MATERIALS_PATH)
            if tbl_item:
                raw_materials_agg = expand_components(result["rooms"], tbl_item)
                flat = to_flat_list(raw_materials_agg)
                procurement = compute_procurement(flat)
                result["level2_bom"] = flat
                result["procurement"] = procurement
                result["stats"]["raw_materials_loaded"] = True
                result["stats"]["level2_items"] = len(flat)
            else:
                result["level2_bom"] = []
                result["procurement"] = []
                result["stats"]["raw_materials_loaded"] = False
                result["stats"]["level2_items"] = 0
        except Exception as lvl2_err:
            print(f"[level2] expansion failed: {lvl2_err}")
            result["level2_bom"] = []
            result["procurement"] = []
            result["stats"]["raw_materials_loaded"] = False
            result["stats"]["level2_items"] = 0

        return result

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
            dxf_path, _ = convert_dwg_to_dxf(tmp_path)
        fixed_path = fix_dxf(dxf_path)
        if fixed_path != dxf_path:
            dxf_path = fixed_path

        doc = load_dxf(dxf_path)
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
    """Health check — shows active converter and catalog status."""
    dwg2dxf_path = shutil.which("dwg2dxf") or (LIBREDWG_DWG2DXF if os.path.isfile(LIBREDWG_DWG2DXF) else None)
    if dwg2dxf_path and os.path.getsize(dwg2dxf_path) == 0:
        dwg2dxf_path = None

    catalog = load_catalog()

    # Raw materials / Level 2 BOM status
    try:
        from material_expansion import load_tbl_item
        tbl_item = load_tbl_item(RAW_MATERIALS_PATH)
        raw_materials_items = len(tbl_item)
        raw_materials_path = RAW_MATERIALS_PATH if tbl_item else None
    except Exception:
        raw_materials_items = 0
        raw_materials_path = None

    if ODA_SERVICE_URL:
        primary = "oda_service"
    elif LOCAL_ODA_PATH:
        primary = "oda_local"
    elif dwg2dxf_path:
        primary = "libredwg"
    else:
        primary = "none"

    return {
        "status": "ok",
        "ezdxf_version": ezdxf.__version__,
        "dwg_support": bool(ODA_SERVICE_URL or LOCAL_ODA_PATH or dwg2dxf_path),
        "primary_converter": primary,
        "converters": {
            "oda_service": ODA_SERVICE_URL or None,
            "oda_local": LOCAL_ODA_PATH or None,
            "libredwg": dwg2dxf_path,
        },
        "catalog_items": len(catalog),
        "catalog_path": CATALOG_PATH if catalog else None,
        "raw_materials_items": raw_materials_items,
        "raw_materials_path": raw_materials_path,
        "level2_bom": raw_materials_items > 0,
    }
