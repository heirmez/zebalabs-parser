FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ── System deps ──────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    libglib2.0-0 \
    libxrender1 libsm6 libxext6 \
    libfontconfig1 libfreetype6 \
    libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 \
    libxkbcommon-x11-0 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

# ── ODA File Converter 27.1 (Qt6, x64) ───────────────────────────────────
# bin/ODAFileConverter.deb = ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb
COPY bin/ODAFileConverter.deb /tmp/ODAFileConverter.deb
RUN apt-get update && apt-get install -y --no-install-recommends /tmp/ODAFileConverter.deb \
    && rm /tmp/ODAFileConverter.deb \
    && rm -rf /var/lib/apt/lists/* \
    && ODAFileConverter --version 2>&1 | head -1 || echo "ODA installed (version check may require display)"

# ── LibreDWG dwg2dxf (fallback) ──────────────────────────────────────────
COPY bin/dwg2dxf /usr/local/bin/dwg2dxf
RUN chmod +x /usr/local/bin/dwg2dxf && dwg2dxf --version && echo "dwg2dxf OK"

# ── Python deps ──────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY main.py .
COPY material_expansion.py .
COPY procurement_logic.py .
COPY *.csv ./

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
