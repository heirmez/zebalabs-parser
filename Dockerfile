FROM python:3.11-slim

WORKDIR /app

# libredwg-utils is in Debian non-free — enable it then install
RUN echo "deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware" \
    > /etc/apt/sources.list.d/bookworm-non-free.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    libredwg-utils \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
