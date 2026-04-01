FROM python:3.11-slim

WORKDIR /app

# libredwg-utils provides dwg2dxf (supports R1.0-R2018 / AC1032)
# Available in Debian Bookworm repos — no compilation needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    libredwg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
