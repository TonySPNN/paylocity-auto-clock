FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Set environment
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Bangkok

WORKDIR /app

# Install system utilities & fonts for Thai language
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    fonts-thai-tlwg \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Ensure screenshot and static directories exist
RUN mkdir -p /app/screenshots /app/static

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
