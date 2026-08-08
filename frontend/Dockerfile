# Purpose: Build stage for serving DocScribe React + Vite + TypeScript frontend.
# Future TODOs: Use multi-stage builds with Nginx or Caddy to serve production bundle assets.

FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
