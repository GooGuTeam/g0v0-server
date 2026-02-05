# g0v0-server

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://docs.astral.sh/ruff/)
[![CodeFactor](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server/badge)](https://www.codefactor.io/repository/github/GooGuTeam/g0v0-server)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/GooGuTeam/g0v0-server/main.svg)](https://results.pre-commit.ci/latest/github/GooGuTeam/g0v0-server/main)
[![license](https://img.shields.io/github/license/GooGuTeam/g0v0-server)](./LICENSE)
[![discord](https://discordapp.com/api/guilds/1404817877504229426/widget.png?style=shield)](https://discord.gg/AhzJXXWYfF)

简体中文 | [English](./README.en.md)

这是一个使用 FastAPI + MySQL + Redis 实现的 osu! API 模拟服务器，支持 osu! API v1, v2 和 osu!lazer 的绝大部分功能。

## 功能特性

- **OAuth 2.0 认证**: 支持密码流和刷新令牌流
- **用户数据管理**: 完整的用户信息、统计数据、成就等
- **多游戏模式支持**: osu! (RX, AP), taiko (RX), catch (RX), mania 和自定义 ruleset（见下）
- **数据库持久化**: MySQL 存储用户数据
- **缓存支持**: Redis 缓存令牌和会话信息
- **多种存储后端**: 支持本地存储、Cloudflare R2、AWS S3
- **容器化部署**: Docker 和 Docker Compose 支持
- **资源文件反向代理**: 可以将 osu! 官方的资源链接（头像、谱面封面、音频等）替换为自定义域名。

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

## 文档

前往 <https://docs.g0v0.top/> 查看。

## 部署

查看[文档](https://docs.g0v0.top/lazer/deploy/deploy-with-docker.html)。

## 安全

使用 `openssl rand -hex 32` 生成 JWT 密钥，以保证服务器安全和旁观服务器的正常运行

使用 `openssl rand -hex 40` 生成前端密钥

**如果是在公网环境下，请屏蔽对 `/_lio` 路径的外部请求**

## 文档

前往 [wiki](https://github.com/GooGuTeam/g0v0-server/wiki) 查看

## 许可证

本项目采用 **GNU Affero General Public License v3.0 (AGPL-3.0-only)** 授权。  
任何衍生作品、修改或部署 **必须在显著位置清晰署名** 原始作者：  
**GooGuTeam - https://github.com/GooGuTeam/g0v0-server**

## 贡献

项目目前处于快速迭代状态，欢迎提交 Issue 和 Pull Request！

查看 [贡献指南](./CONTRIBUTING.md) 获取更多信息。

## 贡献者

<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-7-orange.svg?style=flat-square)](#contributors-)
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
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

本项目遵循 [all-contributors](https://github.com/all-contributors/all-contributors) 规范。欢迎任何形式的贡献！

## 参与讨论

- QQ 群：`1059561526`
- Discord: https://discord.gg/AhzJXXWYfF
