"""ODA File Converter microservice.

Accepts a DWG file via POST /convert, returns the converted DXF bytes.
Deploy this as a separate service built LOCALLY so ODA can be downloaded.
The main parser calls this via ODA_SERVICE_URL env var.
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import subprocess
import tempfile
import os
import glob

app = FastAPI(title="ODA DWG→DXF Converter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ODA_PATH = os.environ.get("ODA_PATH", "/usr/bin/ODAFileConverter")


@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    """Convert a DWG file to DXF using ODA File Converter.

    Returns raw DXF bytes with Content-Disposition header.
    """
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    if not file.filename.lower().endswith(".dwg"):
        raise HTTPException(400, "Only .dwg files accepted")

    if not os.path.isfile(ODA_PATH):
        raise HTTPException(503, f"ODA File Converter not found at {ODA_PATH}")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    with tempfile.TemporaryDirectory(prefix="oda_in_") as in_dir, \
         tempfile.TemporaryDirectory(prefix="oda_out_") as out_dir:

        # Write DWG to input dir
        dwg_path = os.path.join(in_dir, file.filename)
        with open(dwg_path, "wb") as f:
            f.write(content)

        # ODA File Converter args: <input_dir> <output_dir> <version> <format> <recurse> <audit>
        result = subprocess.run(
            [ODA_PATH, in_dir, out_dir, "ACAD2018", "DXF", "0", "1"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # ODA outputs same_filename.dxf in the output dir (preserves input stem)
        expected = os.path.join(out_dir, os.path.splitext(file.filename)[0] + ".dxf")
        dxf_files = [expected] if os.path.isfile(expected) else glob.glob(os.path.join(out_dir, "*.dxf"))
        if not dxf_files or os.path.getsize(dxf_files[0]) == 0:
            detail = f"ODA conversion produced no output. stdout={result.stdout[:300]} stderr={result.stderr[:300]}"
            raise HTTPException(500, detail)

        with open(dxf_files[0], "rb") as f:
            dxf_content = f.read()

        dxf_filename = os.path.splitext(file.filename)[0] + ".dxf"
        return Response(
            content=dxf_content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{dxf_filename}"'},
        )


@app.get("/health")
async def health():
    oda_ok = os.path.isfile(ODA_PATH)
    return {
        "status": "ok" if oda_ok else "degraded",
        "oda": ODA_PATH if oda_ok else None,
    }
