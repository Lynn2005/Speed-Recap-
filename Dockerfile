FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-extra fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY . .
RUN mkdir -p /var/data/lynn-recap/uploads /var/data/lynn-recap/outputs
EXPOSE 10000
CMD ["sh","-c","streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-10000} --server.maxUploadSize 900 --server.maxMessageSize 900 --browser.gatherUsageStats false"]
