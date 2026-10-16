#!/bin/sh
# Starts both FlowCRM processes in the combined container (see Dockerfile).

# API: internal only, reached through the web's /api/v1 rewrite
(cd /app/api/apps/api && PORT=4000 node dist/server.js) &

# Web: the public process on $PORT
cd /app/web && exec node apps/web/server.js
