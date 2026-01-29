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
-   **Multi-game Mode Support**: osu! (RX, AP), taiko (RX), catch (RX), mania and custom rulesets (see below).
-   **Database Persistence**: MySQL for storing user data.
-   **Cache Support**: Redis for caching tokens and session information.
-   **Multiple Storage Backends**: Supports local storage, Cloudflare R2, and AWS S3.
-   **Containerized Deployment**: Docker and Docker Compose support.

## Supported Rulesets

**Ruleset**|**ID**|**ShortName**|**PP Algorithm (rosu)**|**PP Algorithm (performance-server)**
:-----:|:-----:|:-----:|:-----:|:-----:
osu!|`0`|`osu`|✅|✅
osu!taiko|`1`|`taiko`|✅|✅
osu!catch|`2`|`fruits`|✅|✅
osu!mania|`3`|`mania`|✅|✅
osu! (RX)|`4`|`osurx`|✅|✅
osu! (AP)|`5`|`osuap`|✅|✅
osu!taiko (RX)|`6`|`taikorx`|✅|✅
osu!catch (RX)|`7`|`fruitsrx`|✅|✅
[Sentakki](https://github.com/LumpBloom7/sentakki)|`10`|`Sentakki`|❌|❌
[tau](https://github.com/taulazer/tau)|`11`|`tau`|❌|✅
[Rush!](https://github.com/Beamographic/rush)|`12`|`rush`|❌|❌
[hishigata](https://github.com/LumpBloom7/hishigata)|`13`|`hishigata`|❌|❌
[soyokaze!](https://github.com/goodtrailer/soyokaze)|`14`|`soyokaze`|❌|✅

Go to [custom-rulesets](https://github.com/GooGuTeam/custom-rulesets) to download the custom rulesets modified for g0v0-server.

## Documentation

View it at <https://docs.g0v0.top/>

## Deploy

View the [documentation](https://docs.g0v0.top/en/lazer/deploy/deploy-with-docker.html)

## Security

Use `openssl rand -hex 32` to generate the JWT secret key to ensure the security of the server and the normal operation of the observer server.

Use `openssl rand -hex 40` to generate the frontend secret key.

**If it is in a public network environment, please block external requests to the `/_lio` path.**

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0-only)**.  
Any derivative work, modification, or deployment **MUST clearly and prominently attribute** the original authors:  
**GooGuTeam - https://github.com/GooGuTeam/g0v0-server**

## Contributing

The project is currently in a state of rapid iteration. Issues and Pull Requests are welcome!

See [Contributing Guide](./CONTRIBUTING.md) for more information.

## Contributors

<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-6-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/GooGuJiang"><img src="https://avatars.githubusercontent.com/u/74496778?v=4?s=100" width="100px;" alt="咕谷酱"/><br /><sub><b>咕谷酱</b></sub></a><br /><a href="https://github.com/GooGuTeam/g0v0-server/commits?author=GooGuJiang" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://blog.mxgame.top/"><img src="https://avatars.githubusercontent.com/u/68982190?v=4?s=100" width="100px;" alt="MingxuanGame"/><br /><sub><b>MingxuanGame</b></sub></a><br /><a href="https://github.com/GooGuTeam/g0v0-server/commits?author=MingxuanGame" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/chenjintang-shrimp"><img src="https://avatars.githubusercontent.com/u/110657724?v=4?s=100" width="100px;" alt="陈晋瑭"/><br /><sub><b>陈晋瑭</b></sub></a><br /><a href="https://github.com/GooGuTeam/g0v0-server/commits?author=chenjintang-shrimp" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://4ayo.ovh"><img src="https://avatars.githubusercontent.com/u/115783539?v=4?s=100" width="100px;" alt="4ayo"/><br /><sub><b>4ayo</b></sub></a><br /><a href="#ideas-4aya" title="Ideas, Planning, & Feedback">🤔</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/kyiuu1"><img src="https://avatars.githubusercontent.com/u/188347675?v=4?s=100" width="100px;" alt="kyiuu1"/><br /><sub><b>kyiuu1</b></sub></a><br /><a href="#ideas-kyiuu1" title="Ideas, Planning, & Feedback">🤔</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/CloneWith"><img src="https://avatars.githubusercontent.com/u/110881926?v=4?s=100" width="100px;" alt="复予"/><br /><sub><b>复予</b></sub></a><br /><a href="https://github.com/GooGuTeam/g0v0-server/commits?author=CloneWith" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!

## Discussion

- Discord: https://discord.gg/AhzJXXWYfF
- QQ Group: `1059561526`
