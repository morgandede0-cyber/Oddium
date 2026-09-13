#!/bin/sh
set -e
mkdir -p data data/backups data/api_cache_propline logs
docker compose up -d --build
docker compose ps
