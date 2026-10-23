FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && apt-get update && apt-get install -y --no-install-recommends bash ca-certificates git ripgrep zip unzip && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp/home NPM_CONFIG_CACHE=/tmp/npm PYTHONPYCACHEPREFIX=/tmp/pycache
WORKDIR /workspace
CMD ["sleep","infinity"]
