# g0v0-server

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://docs.astral.sh/ruff/)
[![CodeFactor](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server/badge)](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/GooGuTeam/g0v0-server/main.svg)](https://results.pre-commit.ci/latest/github/GooGuTeam/g0v0-server/main)
[![license](https://img.shields.io/github/license/GooGuTeam/g0v0-server)](./LICENSE)
[![discord](https://discordapp.com/api/guilds/1404817877504229426/widget.png?style=shield)](https://discord.gg/AhzJXXWYfF)
[![docs](https://img.shields.io/badge/docs-latest-blue)](https://docs.g0v0.top/)

English | [简体中文](./README.zh-cn.md)

g0v0-server is an osu!(lazer) server written in Python that supports the latest osu!(lazer) client and provides additional features (such as Relax/Autopilot Mod statistics and custom ruleset support).

g0v0-server is implemented based on osu! API v2 and is largely compatible with both osu! API v1 and v2. This means you can easily integrate existing osu! applications with g0v0-server.

Additionally, g0v0-server provides a set of g0v0! APIs to enable operations beyond the osu! API.

g0v0-server is not just a score server. It implements most of the features of the osu! website (such as chat, user settings, etc.).

We provide a demo server at <https://lazer-api.g0v0.top> (frontend at <https://lazer.g0v0.top>). You can experience g0v0-server's features through our demo server.

## Features

- Supports the latest osu!(lazer) client
- Supports Relax/Autopilot Mod statistics
- Supports [custom rulesets](#supported-rulesets)
- Supports [plugins](#plugins), allowing developers to add new features to the server
- Supports [Docker deployment](https://docs.g0v0.top/en/lazer/deploy/deploy-with-docker.html)

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

Visit [custom-rulesets](https://github.com/GooGuTeam/custom-rulesets/releases/latest) to download custom rulesets modified for g0v0-server.

## Documentation & Quick Start

Visit <https://docs.g0v0.top/> for more information.

## Plugins

g0v0-server supports plugins, allowing developers to add new features to the server. See [Managing Plugins](https://docs.g0v0.top/en/lazer/deploy/manage-plugins.html) to install plugins, and see the [Plugin Development Guide](https://docs.g0v0.top/en/lazer/development/plugin/) to develop plugins.

## Security

Use `openssl rand -hex 32` to generate the JWT secret key to ensure server security and proper operation of the spectator server.

Use `openssl rand -hex 40` to generate the frontend secret key.

**If running in a public network environment, please block external requests to the `/_lio` path.**

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0-only)**.  
Any derivative work, modification, or deployment **MUST clearly and prominently attribute** the original authors:  
> **GooGuTeam - https://github.com/GooGuTeam/g0v0-server**

## Contributing

The project is currently in a state of rapid iteration. Issues and Pull Requests are welcome!

See [Contributing Guide](./CONTRIBUTING.md) for more information.

## Contributors

<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-7-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

Thanks to all the contributors to this project! ([emoji key](https://allcontributors.org/docs/en/emoji-key))

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
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/ShikkesoraSIM"><img src="https://avatars.githubusercontent.com/u/148418529?v=4?s=100" width="100px;" alt="Shikkesora"/><br /><sub><b>Shikkesora</b></sub></a><br /><a href="https://github.com/GooGuTeam/g0v0-server/issues?q=author%3AShikkesoraSIM" title="Bug reports">🐛</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind are welcome!

## Discussion

- Discord: https://discord.gg/AhzJXXWYfF
- QQ Group: `1059561526`
