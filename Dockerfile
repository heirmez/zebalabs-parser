# Stage 1: Build LibreDWG from source
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential autoconf automake libtool pkg-config wget xz-utils perl \
    && rm -rf /var/lib/apt/lists/*

# Create stub first so COPY in stage-2 always succeeds even if build fails
RUN touch /usr/local/bin/dwg2dxf && chmod +x /usr/local/bin/dwg2dxf

# Build dwg2dxf — overwrites stub on success, stub remains on failure
RUN wget -q https://github.com/LibreDWG/libredwg/releases/download/0.12.5/libredwg-0.12.5.tar.xz \
    && tar xf libredwg-0.12.5.tar.xz \
    && cd libredwg-0.12.5 \
    && ./configure \
    && make -j$(nproc) \
    && cp programs/dwg2dxf /usr/local/bin/dwg2dxf \
    && chmod +x /usr/local/bin/dwg2dxf \
    && strip /usr/local/bin/dwg2dxf 2>/dev/null \
    || echo "LibreDWG build failed - stub remains, ODA will be used"

# Stage 2: Runtime image
FROM python:3.11-slim

WORKDIR /app

# System libs for Python/ezdxf
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 wget \
    && rm -rf /var/lib/apt/lists/*

# Install ODA File Converter (primary DWG→DXF for AC1032 / AutoCAD 2018+)
# Try multiple known versions — ODA's download URL format: ODAFileConverter_QT5_lnxX64_8.3dll_YY.MM.deb
RUN apt-get update \
    && (wget -q "https://download.opendesign.com/guestfiles/Demo/ODAFileConverter_QT5_lnxX64_8.3dll_25.5.deb" -O /tmp/odafc.deb \
        || wget -q "https://download.opendesign.com/guestfiles/Demo/ODAFileConverter_QT5_lnxX64_8.3dll_24.12.deb" -O /tmp/odafc.deb \
        || wget -q "https://download.opendesign.com/guestfiles/Demo/ODAFileConverter_QT5_lnxX64_8.3dll_24.6.deb" -O /tmp/odafc.deb) \
    && apt-get install -y /tmp/odafc.deb \
    && rm -f /tmp/odafc.deb \
    && rm -rf /var/lib/apt/lists/* \
    || (rm -f /tmp/odafc.deb && apt-get clean && echo "ODA install failed - LibreDWG fallback only")

# Copy LibreDWG dwg2dxf (real binary or stub — COPY always succeeds)
COPY --from=builder /usr/local/bin/dwg2dxf /usr/local/bin/dwg2dxf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
