FROM node:20-bookworm-slim
ENV NODE_ENV=production
ENV PORT=10000
ENV DATA_DIR=/var/data/speed-recap
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-extra fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY . .
RUN mkdir -p /var/data/speed-recap/uploads /var/data/speed-recap/outputs
EXPOSE 10000
CMD ["npm","start"]