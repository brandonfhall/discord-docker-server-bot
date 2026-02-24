# Valheim Docker Controller Discord Bot

This project provides a Dockerized Discord bot to start/stop/restart a Valheim server container, announce shutdowns in Discord and in-game, and expose a local status endpoint.

Quick start:

1. Copy `.env.example` to `.env` and fill in values (Discord bot token, allowed containers, roles).
2. Build the image and run the bot (example uses docker-compose):

```bash
docker compose up -d --build
```

Configuration and notes are in `README.md` and `src/config.py`.
