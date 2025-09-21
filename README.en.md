# g0v0-server

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://docs.astral.sh/ruff/)
[![CodeFactor](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server/badge)](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/GooGuTeam/g0v0-server/main.svg)](https://results.pre-commit.ci/latest/github/GooGuTeam/g0v0-server/main)
[![license](https://img.shields.io/github/license/GooGuTeam/g0v0-server)](./LICENSE)
[![discord](https://discordapp.com/api/guilds/1404817877504229426/widget.png?style=shield)](https://discord.gg/AhzJXXWYfF)

[简体中文](./README.md) | English

This is an osu! API server implemented with FastAPI + MySQL + Redis, supporting most features of osu! API v1, v2, and osu!lazer.

## Features

-   **OAuth 2.0 Authentication**: Supports password and refresh token flows.
-   **User Data Management**: Complete user information, statistics, achievements, etc.
-   **Multi-game Mode Support**: osu! (RX, AP), taiko (RX), catch (RX), mania.
-   **Database Persistence**: MySQL for storing user data.
-   **Cache Support**: Redis for caching tokens and session information.
-   **Multiple Storage Backends**: Supports local storage, Cloudflare R2, and AWS S3.
-   **Containerized Deployment**: Docker and Docker Compose support.

## Quick Start

### Using Docker Compose (Recommended)

1.  Clone the project
    ```bash
    git clone https://github.com/GooGuTeam/g0v0-server.git
    cd g0v0-server
    ```
2.  Create a `.env` file

    Please see [wiki](https://github.com/GooGuTeam/g0v0-server/wiki/Configuration) to modify the .env file.
    ```bash
    cp .env.example .env
    ```
3.  Start the service
    ```bash
    # Standard server
    docker-compose -f docker-compose.yml up -d
    # Enable osu!RX and osu!AP statistics (Gu pp algorithm based on ppy-sb pp algorithm)
    docker-compose -f docker-compose-osurx.yml up -d
    ```
4.  Connect to the server from the game

    Use a [custom osu!lazer client](https://github.com/GooGuTeam/osu), or use [LazerAuthlibInjection](https://github.com/MingxuanGame/LazerAuthlibInjection), and change the server settings to the server's address.

### Updating the Database

Refer to the [Database Migration Guide](https://github.com/GooGuTeam/g0v0-server/wiki/Migrate-Database)

## Security

Use `openssl rand -hex 32` to generate the JWT secret key to ensure the security of the server and the normal operation of the observer server.

Use `openssl rand -hex 40` to generate the frontend secret key.

**If it is in a public network environment, please block external requests to the `/_lio` path.**

## Documentation

Visit the [wiki](https://github.com/GooGuTeam/g0v0-server/wiki) for more information.

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0-only)**.  
Any derivative work, modification, or deployment **MUST clearly and prominently attribute** the original authors:  
**GooGuTeam - https://github.com/GooGuTeam/g0v0-server**

## Contributing

The project is currently in a state of rapid iteration. Issues and Pull Requests are welcome!

See [Contributing Guide](./CONTRIBUTING.md) for more information.

## Discussion

- Discord: https://discord.gg/AhzJXXWYfF
- QQ Group: `1059561526`
