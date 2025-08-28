# 贡献指南

## 克隆项目

```bash
git clone https://github.com/GooGuTeam/g0v0-server.git
```

此外，您还需要 clone 一个 spectator-server 到 g0v0-server 的文件夹。

```bash
git clone https://github.com/GooGuTeam/osu-server-spectator.git spectator-server
```

## 开发环境

为了确保一致的开发环境，我们强烈建议使用提供的 Dev Container。这将设置一个容器化的环境，预先安装所有必要的工具和依赖项。

1.  安装 [Docker](https://www.docker.com/products/docker-desktop/)。
2.  在 Visual Studio Code 中安装 [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)。
3.  在 VS Code 中打开项目。当被提示时，点击“在容器中重新打开”以启动开发容器。

## 配置项目

修改 `.env` 配置（参考 [wiki](https://github.com/GooGuTeam/g0v0-server/wiki/Configuration)），生成并填充 JWT 密钥。

如果在 Dev Container 运行，请修改 `MYSQL_HOST` 为 `mysql`，`REDIS_URL` 为 `redis://redis/0`。

## 启动项目

.devcontainer 文件夹提供了一个启动脚本 `start-dev.sh`，这个脚本会从 `.env` 加载环境变量并同时启动 g0v0-server（端口 `8000`）和 spectator-server（端口 `8006`）。

Dev Container 提供了 NGINX 进行转发，对外访问端口是 `8080`。

如果您的服务器没有配置 HTTPS，可以在启动 osu! 的时候指定环境变量 `OSU_INSECURE_REQUESTS=1` 禁用 SSL 检查，或者应用 [osu!lazer wiki](https://github.com/ppy/osu/wiki/Testing-web-server-full-stack-with-osu!#basics) 提供的 diff。

或者使用下方的命令手动启动：

```bash
# g0v0-server
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# spectator-server
cd spectator-server
dotnet run --project osu.Server.Spectator --urls "http://0.0.0.0:8086"
```

## 依赖管理

使用 `uv` 进行快速高效的 Python 包管理。

要安装依赖项，请在终端中运行以下命令：

```bash
uv sync
```

## 代码质量和代码检查

我们使用 `pre-commit` 在提交之前执行代码质量标准。这确保所有代码都通过 `ruff`（用于代码检查和格式化）和 `pyright`（用于类型检查）的检查。

### 设置

要设置 `pre-commit`，请运行以下命令：

```bash
pre-commit install
```

这将安装 pre-commit 钩子，每次提交时会自动运行。如果任何检查失败，提交将被中止。您需要修复报告的问题并暂存更改，然后再尝试提交。

pre-commit 不提供 pyright 的 hook，您需要手动运行 `pyright` 检查类型错误。

## 提交信息指南

我们遵循 [AngularJS 提交规范](https://github.com/angular/angular.js/blob/master/DEVELOPERS.md#commit-message-format) 来编写提交信息。这使得在查看项目历史记录时，信息更加可读且易于理解。

每条提交信息由 **标题**、**主体**和 **页脚** 三部分组成。

```
<type>(<scope>): <subject>
<BLANK LINE>
<body>
<BLANK LINE>
<footer>
```

**类型** 必须是以下之一：

*   **feat**：新功能
*   **fix**：错误修复
*   **docs**：仅文档更改
*   **style**：不影响代码含义的更改（空格、格式、缺少分号等）
*   **refactor**：代码重构
*   **perf**：改善性能的代码更改
*   **test**：添加缺失的测试或修正现有测试
*   **chore**：对构建过程或辅助工具和库（如文档生成）的更改
*   **ci**：持续集成相关的更改
*   **deploy**: 部署相关的更改

**范围** 可以是任何指定提交更改位置的内容。例如 `api`、`db`、`auth` 等等。

**主题** 包含对更改的简洁描述。

## 项目结构

以下是项目主要目录和文件的结构说明：

-   `main.py`: FastAPI 应用的主入口点，负责初始化和启动服务器。
-   `pyproject.toml`: 项目配置文件，用于管理依赖项 (uv)、代码格式化 (Ruff) 和类型检查 (Pyright)。
-   `alembic.ini`: Alembic 数据库迁移工具的配置文件。
-   `app/`: 存放所有核心应用代码。
    -   `router/`: 包含所有 API 端点的定义，根据 API 版本和功能进行组织。
    -   `service/`: 存放核心业务逻辑，例如用户排名计算、每日挑战处理等。
    -   `database/`: 定义数据库模型 (SQLModel) 和会话管理。
    -   `models/`: 定义非数据库模型和其他模型。
    -   `scheduler/`: 包含由 APScheduler 调度的后台任务。
    -   `dependencies/`: 管理 FastAPI 的依赖项注入。
    -   `achievement.py`: 存放与成就相关的逻辑。
    -   `storage/`: 存储服务代码。
    -   `fetcher/`: 用于从外部服务（如 osu! 官网）获取数据的模块。
    -   `config.py`: 应用配置，使用 pydantic-settings 管理。
    -   `calculator.py`: 存放所有的计算逻辑，例如 pp 和等级。
-   `migrations/`: 存放 Alembic 生成的数据库迁移脚本。
-   `packages/`: 包含项目相关的独立包。
    -   `msgpack_lazer_api/`: 基于 Rust 的高性能支持 lazer APIMod 的 MessagePack 解析模块，用于与 osu!lazer 客户端通信。
-   `static/`: 存放静态文件，如 `mods.json`。

## API Router 规范

- `app/router/v2` 存放所有 osu! v2 API 实现，不允许添加额外的，原 v2 API 不存在的 Endpoint
- `app/router/notification` 存放所有 osu! v2 API 聊天和通知的实现，不允许添加额外的，原 v2 API 不存在的 Endpoint
- `app/router/v1` 存放所有 osu! v1 API 实现，不允许添加额外的，原 v1 API 不存在的 Endpoint
- `app/router/auth.py` 存放账户鉴权/登录的 API
- `app/router/private` 存放服务器自定义 API，供其他服务使用

感谢您的贡献！
