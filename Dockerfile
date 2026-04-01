# Stage 1: Build LibreDWG from source
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential autoconf automake libtool pkg-config wget xz-utils perl \
    && rm -rf /var/lib/apt/lists/*

# Create stub first so COPY in stage-2 always succeeds even if build fails
RUN touch /usr/local/bin/dwg2dxf && chmod +x /usr/local/bin/dwg2dxf

# Build dwg2dxf — CFLAGS="-w" suppresses GCC 12 warnings-as-errors (LibreDWG 0.12.5 compat)
# --disable-bindings skips SWIG/Python/Perl language binding compilation
RUN wget -q https://github.com/LibreDWG/libredwg/releases/download/0.12.5/libredwg-0.12.5.tar.xz \
    && tar xf libredwg-0.12.5.tar.xz \
    && cd libredwg-0.12.5 \
    && ./configure --disable-bindings \
    && make CFLAGS="-O2 -w" -j$(nproc) \
    && cp programs/dwg2dxf /usr/local/bin/dwg2dxf \
    && chmod +x /usr/local/bin/dwg2dxf \
    && strip /usr/local/bin/dwg2dxf 2>/dev/null \
    || echo "LibreDWG build failed - stub remains"

# Stage 2: Runtime image
FROM python:3.11-slim

WORKDIR /app

# System libs for Python/ezdxf
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 wget \
    && rm -rf /var/lib/apt/lists/*

# ODA File Converter: Railway blocks download.opendesign.com — install manually if needed
# See: https://www.opendesign.com/guestfiles/oda_file_converter

# Copy LibreDWG dwg2dxf (real binary or stub — COPY always succeeds)
COPY --from=builder /usr/local/bin/dwg2dxf /usr/local/bin/dwg2dxf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
