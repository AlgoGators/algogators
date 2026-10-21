# The AlgoLens frontend: a static Vite build served by nginx.
#
# Build context is the REPO ROOT (the app is a member of the pnpm workspace, so the lockfile
# and workspace manifest live above it):
#   docker build -f apps/algolens-web/Dockerfile --build-arg VITE_API_URL=https://algolens.algogators.com .
FROM node:26-alpine AS build

RUN corepack enable

WORKDIR /repo

# Manifests first so the dependency layer is cached until a dependency changes. pnpm checks the
# whole lockfile, so every workspace member's manifest has to be present.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/algolens-web/package.json apps/algolens-web/package.json
COPY apps/website/package.json apps/website/package.json
RUN pnpm install --frozen-lockfile --filter @algogators/algolens-web...

COPY apps/algolens-web apps/algolens-web

# The API origin is baked into the static build; the site is same-origin behind the edge nginx.
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

RUN pnpm --filter @algogators/algolens-web run build

FROM nginx:alpine
COPY --from=build /repo/apps/algolens-web/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
