FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Set environment
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Bangkok
ENV PORT=8000

WORKDIR /app

# Install system utilities & fonts for Thai language non-interactively
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    fonts-thai-tlwg \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .
RUN mkdir -p /app/screenshots /app/static

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
