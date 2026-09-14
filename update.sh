#!/bin/sh
set -e
mkdir -p data data/backups data/api_cache logs
docker compose up -d --build
docker compose ps
