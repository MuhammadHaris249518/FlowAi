# FlowCRM combined image, built from the REPO ROOT (paths prefixed with flowcrm/).
# Alternate Clouds always builds the root Dockerfile, so the flowcrm-deploy branch
# copies this file to ./Dockerfile; main keeps the Talkse Dockerfile at the root.
#   web (Next.js, public, PORT 3000)  ->  /api/v1/* rewritten to  ->  API (Express, internal 4000)

FROM node:20-alpine AS builder
WORKDIR /app
RUN apk add --no-cache openssl libc6-compat

COPY flowcrm/package*.json ./
COPY flowcrm/apps/api/package*.json ./apps/api/
COPY flowcrm/apps/web/package*.json ./apps/web/
COPY flowcrm/apps/api/src/prisma ./apps/api/src/prisma
RUN npm ci --workspace=apps/api --workspace=apps/web --no-audit --no-fund
RUN cd apps/api && npx prisma generate --schema=src/prisma/schema.prisma

COPY flowcrm/apps/api ./apps/api
COPY flowcrm/apps/web ./apps/web
RUN cd apps/api && npm run build

# Baked into the web bundle at build time: same-origin API + no Clerk login
ENV NEXT_PUBLIC_API_URL=/api/v1 \
    NEXT_PUBLIC_AUTH_MODE=demo \
    NEXT_PUBLIC_TALKSE_URL=https://alternate-vivid-tower-7542-github-app.alternatefutures.ai \
    NEXT_TELEMETRY_DISABLED=1
RUN cd apps/web && npx next build

FROM node:20-alpine AS runner
RUN apk add --no-cache openssl libc6-compat
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/apps/web/.next/standalone ./web
COPY --from=builder /app/apps/web/.next/static ./web/apps/web/.next/static
COPY --from=builder /app/apps/web/public ./web/apps/web/public

COPY --from=builder /app/node_modules ./api/node_modules
COPY --from=builder /app/apps/api/dist ./api/apps/api/dist
COPY --from=builder /app/apps/api/package*.json ./api/apps/api/
COPY --from=builder /app/apps/api/src/prisma ./api/apps/api/src/prisma

COPY flowcrm/start.sh ./start.sh
# Strip Windows line endings: a CRLF start.sh fails under Linux sh ("\r: not found")
RUN sed -i 's/\r$//' ./start.sh

# Must match the port the Alternate Clouds service publishes
ENV PORT=3000 \
    HOSTNAME=0.0.0.0
EXPOSE 3000

CMD ["sh", "./start.sh"]
