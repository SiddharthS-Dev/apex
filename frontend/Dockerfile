# ---------------------------------------------------------------
# APEX frontend
# ---------------------------------------------------------------
FROM node:22-alpine AS deps

WORKDIR /srv/apex

COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit

# ---------------------------------------------------------------
# Vite dev server with hot reload; sources are bind-mounted by compose.
FROM deps AS development

COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---------------------------------------------------------------
FROM deps AS build

COPY . .
RUN npm run build

# ---------------------------------------------------------------
# Static bundle served by nginx.
FROM nginx:1.27-alpine AS production

COPY --from=build /srv/apex/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
