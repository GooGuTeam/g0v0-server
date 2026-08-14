# g0v0-server

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://docs.astral.sh/ruff/)
[![CodeFactor](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server/badge)](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/GooGuTeam/g0v0-server/main.svg)](https://results.pre-commit.ci/latest/github/GooGuTeam/g0v0-server/main)
[![license](https://img.shields.io/github/license/GooGuTeam/g0v0-server)](./LICENSE)
[![discord](https://discordapp.com/api/guilds/1404817877504229426/widget.png?style=shield)](https://discord.gg/AhzJXXWYfF)
[![docs](https://img.shields.io/badge/docs-latest-blue)](https://docs.g0v0.top/)

[English](./README.md) | 简体中文

g0v0-server 是一个使用 Python 编写的 osu!(lazer) 服务器，支持最新的 osu!(lazer) 客户端并提供了额外功能（例如 Relax/Autopilot Mod 统计信息、自定义 ruleset 支持）。

g0v0-server 根据 osu! API v2 实现，对 osu! API v1 和 v2 实现了绝大多数兼容。这意味着你可以轻易将现有的 osu! 应用程序接入 g0v0-server。

同时 g0v0-server 也提供了一系列 g0v0! API 以在 osu! API 之外实现对其他功能的操作。

g0v0-server 不仅是一个成绩服务器。它实现了大部分的 osu! 网站的功能（例如聊天、用户设置等）。

我们提供了一个实例服务器 <https://lazer-api.g0v0.top>（前端 <https://lazer.g0v0.top>），你可以通过我们的实例服务器来体验 g0v0-server 的功能。

## 特性

- 支持最新的 osu!(lazer) 客户端
- 支持 Relax/Autopilot Mod 统计信息
- 支持[自定义 ruleset](#支持的-ruleset)
- 支持[插件](#插件)，允许开发者为服务器添加新的功能
- 支持 [Docker 部署](https://docs.g0v0.top/lazer/deploy/deploy-with-docker.html)

## 支持的 ruleset

**Ruleset**|**ID**|**ShortName**|**PP 算法 (rosu)**|**PP 算法 (performance-server)**
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

前往 [custom-rulesets](https://github.com/GooGuTeam/custom-rulesets/releases/latest) 下载为 g0v0-server 修改的自定义 ruleset。

## 文档及快速开始

前往 <https://docs.g0v0.top/> 查看。

## 插件

g0v0-server 支持插件，允许开发者为服务器添加新的功能。请查阅[管理插件](https://docs.g0v0.top/lazer/deploy/manage-plugins.html)来安装插件，查阅[插件开发指南](https://docs.g0v0.top/lazer/development/plugin/)来开发插件。

## 安全

使用 `openssl rand -hex 32` 生成 JWT 密钥，以保证服务器安全和旁观服务器的正常运行

使用 `openssl rand -hex 40` 生成前端密钥

**如果是在公网环境下，请屏蔽对 `/_lio` 路径的外部请求**

## 许可证

本项目采用 **GNU Affero General Public License v3.0 (AGPL-3.0-only)** 授权。  
任何衍生作品、修改或部署 **必须在显著位置清晰署名** 原始作者：  
> **GooGuTeam - https://github.com/GooGuTeam/g0v0-server**

## 贡献

项目目前处于快速迭代状态，欢迎提交 Issue 和 Pull Request！

查看 [贡献指南](./CONTRIBUTING.md) 获取更多信息。

## 贡献者

<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-8-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

感谢所有参与此项目的贡献者！ ([emoji key](https://allcontributors.org/docs/en/emoji-key))

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
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/ShiruWeb"><img src="https://avatars.githubusercontent.com/u/253860104?v=4?s=100" width="100px;" alt="Shiru"/><br /><sub><b>Shiru</b></sub></a><br /><a href="#security-ShiruWeb" title="Security">🛡️</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

本项目遵循 [all-contributors](https://github.com/all-contributors/all-contributors) 规范。欢迎任何形式的贡献！

## 参与讨论

- QQ 群：`1059561526`
- Discord: https://discord.gg/AhzJXXWYfF
