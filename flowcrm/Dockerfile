# Combined FlowCRM image for Alternate Clouds (hackathon demo):
#   web (Next.js, public, PORT 3000)  ->  /api/v1/* rewritten to  ->  API (Express, internal 4000)
# One service, one public URL. The separate apps/api and apps/web Dockerfiles
# (used by docker-compose) are unchanged.

FROM node:20-alpine AS builder
WORKDIR /app
RUN apk add --no-cache openssl libc6-compat

COPY package*.json ./
COPY apps/api/package*.json ./apps/api/
COPY apps/web/package*.json ./apps/web/
COPY apps/api/src/prisma ./apps/api/src/prisma
RUN npm ci --workspace=apps/api --workspace=apps/web --no-audit --no-fund
RUN cd apps/api && npx prisma generate --schema=src/prisma/schema.prisma

COPY apps/api ./apps/api
COPY apps/web ./apps/web
RUN cd apps/api && npm run build

# Baked into the web bundle at build time: same-origin API + no Clerk login
ENV NEXT_PUBLIC_API_URL=/api/v1 \
    NEXT_PUBLIC_AUTH_MODE=demo \
    NEXT_TELEMETRY_DISABLED=1
RUN cd apps/web && npx next build

FROM node:20-alpine AS runner
RUN apk add --no-cache openssl libc6-compat
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# Web (Next.js standalone output)
COPY --from=builder /app/apps/web/.next/standalone ./web
COPY --from=builder /app/apps/web/.next/static ./web/apps/web/.next/static
COPY --from=builder /app/apps/web/public ./web/apps/web/public

# API (compiled TypeScript + its node_modules, incl. the generated Prisma client)
COPY --from=builder /app/node_modules ./api/node_modules
COPY --from=builder /app/apps/api/dist ./api/apps/api/dist
COPY --from=builder /app/apps/api/package*.json ./api/apps/api/
COPY --from=builder /app/apps/api/src/prisma ./api/apps/api/src/prisma

COPY start.sh ./start.sh

# Must match the PORT set in the Alternate Clouds Source tab (else 502 Bad Gateway)
ENV PORT=3000 \
    HOSTNAME=0.0.0.0
EXPOSE 3000

CMD ["sh", "./start.sh"]
