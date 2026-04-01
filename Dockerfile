FROM python:3.11-slim

WORKDIR /app

# Enable non-free repos (libredwg-utils is in Debian non-free)
# Handles both DEB822 format (Bookworm) and legacy sources.list
RUN sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/ bookworm main$/ bookworm main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    libredwg-utils \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
