FROM python:3.11-slim-bookworm

WORKDIR /app

# ── System deps ──────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libxrender1 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# ── ODA File Converter (primary DWG converter) ───────────────────────────
# To enable: place the Linux .deb in bin/ODAFileConverter.deb
# Download from https://www.opendesign.com/guestfiles/oda_file_converter (free, requires ODA account)
# If not present, parser falls back to LibreDWG.
COPY bin/ ./bin/
RUN if [ -f bin/ODAFileConverter.deb ]; then \
      apt-get update && apt-get install -y --no-install-recommends ./bin/ODAFileConverter.deb \
      && rm -rf /var/lib/apt/lists/* \
      && echo "ODA File Converter installed: $(ODAFileConverter --version 2>&1 | head -1)"; \
    else \
      echo "bin/ODAFileConverter.deb not found — using LibreDWG fallback"; \
    fi

# ── LibreDWG dwg2dxf (fallback DWG converter) ────────────────────────────
RUN if [ -f bin/dwg2dxf ]; then \
      cp bin/dwg2dxf /usr/local/bin/dwg2dxf \
      && chmod +x /usr/local/bin/dwg2dxf \
      && dwg2dxf --version \
      && echo "dwg2dxf OK"; \
    else \
      echo "bin/dwg2dxf not found"; \
    fi

# ── Python deps ──────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
