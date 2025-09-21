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
- **多游戏模式支持**: osu! (RX, AP), taiko (RX), catch (RX), mania
- **数据库持久化**: MySQL 存储用户数据
- **缓存支持**: Redis 缓存令牌和会话信息
- **多种存储后端**: 支持本地存储、Cloudflare R2、AWS S3
- **容器化部署**: Docker 和 Docker Compose 支持
- **资源文件反向代理**: 可以将 osu! 官方的资源链接（头像、谱面封面、音频等）替换为自定义域名。

## 快速开始

### 使用 Docker Compose (推荐)

1. 克隆项目
```bash
git clone https://github.com/GooGuTeam/g0v0-server.git
cd g0v0-server
```

2. 创建 `.env` 文件

请参考 [wiki](https://github.com/GooGuTeam/g0v0-server/wiki/Configuration) 来修改 `.env` 文件

```bash
cp .env.example .env
```

3. 启动服务
```bash
# 标准服务器
docker-compose -f docker-compose.yml up -d
# 启用 osu!RX 和 osu!AP 模式 （基于偏偏要上班 pp 算法的 Gu pp 算法）
docker-compose -f docker-compose-osurx.yml up -d
```

4. 通过游戏连接服务器

使用[自定义的 osu!lazer 客户端](https://github.com/GooGuTeam/osu)，或者使用 [LazerAuthlibInjection](https://github.com/MingxuanGame/LazerAuthlibInjection)，修改服务器设置为服务器的 IP

## 更新数据库

参考[数据库迁移指南](https://github.com/GooGuTeam/g0v0-server/wiki/Migrate-Database)

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

## 参与讨论

- QQ 群：`1059561526`
- Discord: https://discord.gg/AhzJXXWYfF
