# 贡献指南

## 克隆项目

```bash
git clone https://github.com/GooGuTeam/g0v0-server.git
```

此外，您还需要：

- clone 旁观服务器到 g0v0-server 的文件夹。
```bash
git clone https://github.com/GooGuTeam/osu-server-spectator.git spectator-server
```
- clone 表现分计算器到 g0v0-server 的文件夹。

```bash
git clone https://github.com/GooGuTeam/osu-performance-server.git performance-server
```
- 下载并放置自定义规则集 DLL 到 `rulesets/` 目录（如果需要）。

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

## 开发规范

### 项目结构

以下是项目主要目录和文件的结构说明：

-   `main.py`: FastAPI 应用的主入口点，负责初始化和启动服务器。
-   `pyproject.toml`: 项目配置文件，用于管理依赖项 (uv)、代码格式化 (Ruff) 和类型检查 (Pyright)。
-   `alembic.ini`: Alembic 数据库迁移工具的配置文件。
-   `app/`: 存放所有核心应用代码。
    -   `router/`: 包含所有 API 端点的定义，根据 API 版本和功能进行组织。
    -   `service/`: 存放核心业务逻辑，例如用户排名计算、每日挑战处理等。
    -   `database/`: 定义数据库模型 (SQLModel) 和会话管理。
    -   `models/`: 定义非数据库模型和其他模型。
    -   `tasks/`: 包含由 APScheduler 调度的后台任务和启动/关闭任务。
    -   `dependencies/`: 管理 FastAPI 的依赖项注入。
    -   `achievements/`: 存放与成就相关的逻辑。
    -   `storage/`: 存储服务代码。
    -   `fetcher/`: 用于从外部服务（如 osu! 官网）获取数据的模块。
    -   `middleware/`: 定义中间件，例如会话验证。
    -   `helpers/`: 存放辅助函数和工具类。
    -   `config.py`: 应用配置，使用 pydantic-settings 管理。
    -   `calculator.py`: 存放所有的计算逻辑，例如 pp 和等级。
    -   `log.py`: 日志记录模块，提供统一的日志接口。
    -   `const.py`: 定义常量。
    -   `path.py`: 定义跨文件使用的常量。
-   `migrations/`: 存放 Alembic 生成的数据库迁移脚本。
-   `static/`: 存放静态文件，如 `mods.json`。

### 数据库模型定义

所有的数据库模型定义在 `app.database` 里，并且在 `__init__.py` 中导出。

如果这个模型的数据表结构和响应不完全相同，遵循 `Base` - `Table` - `Resp` 结构：

```python
class ModelBase(SQLModel):
    # 定义共有内容
    ...


class Model(ModelBase, table=True):
    # 定义数据库表内容
    ...


class ModelResp(ModelBase):
    # 定义响应内容
    ...

    @classmethod
    def from_db(cls, db: Model) -> "ModelResp":
        # 从数据库模型转换
        ...
```

数据库模块名应与表名相同，定义了多个模型的除外。

如果你需要使用 Session，使用 `app.dependencies.database` 提供的 `with_db`，注意手动使用 `COMMIT`。

```python
from app.dependencies.database import with_db

async with with_db() as session:
    ...
```

### Redis

根据你需要的用途选择对应的 Redis 客户端。如果你的用途较为复杂或趋向一个较大的系统，考虑再创建一个 Redis 连接。

- `redis_client` (db0)：标准用途，存储字符串、哈希等常规数据。
- `redis_message_client` (db1)：用于消息缓存，存储聊天记录等。
- `redis_binary_client` (db2)：用于存储二进制数据，如音频文件等。
- `redis_rate_limit_client` (db3)：仅用于 FastAPI-Limiter 使用。

### API Router

所有的 API Router 定义在 `app.router` 里：

- `app/router/v2` 存放所有 osu! v2 API 实现，**不允许添加额外的，原 v2 API 不存在的 Endpoint**
- `app/router/notification` **存放所有 osu! v2 API 聊天、通知和 BanchoBot 的实现，不允许添加额外的，原 v2 API 不存在的 Endpoint**
- `app/router/v1` 存放所有 osu! v1 API 实现，**不允许添加额外的，原 v1 API 不存在的 Endpoint**
- `app/router/auth.py` 存放账户鉴权/登录的 API
- `app/router/private` 存放服务器自定义 API (g0v0 API)，供其他服务使用

任何 Router 需要满足：

- 使用 Annotated-style 的依赖注入
- 对于已经存在的依赖注入如 Database 和 Redis，使用 `app.dependencies` 中的实现
- 需要拥有文档
- 如果返回需要资源代理，使用 `app.helpers.asset_proxy_helper` 的 `asset_proxy_response` 装饰器。
- 如果需要记录日志，请使用 `app.log` 提供的 `log` 函数获取一个 logger 实例

#### 鉴权

如果这个 Router 可以为公开使用（客户端、前端、OAuth 程序），考虑使用 `Security(get_current_user, scopes=["some_scope"])`，例如：

```python
from typing import Annotated
from fastapi import Security
from app.dependencies.user import get_current_user


@router.get("/some-api")
async def _(current_user: Annotated[User, Security(get_current_user, scopes=["public"])]):
    ...
```

其中 scopes 选择请参考 [`app.dependencies.user`](./app/dependencies/user.py) 的 `oauth2_code` 中的 `scopes`。

如果这个 Router 仅限客户端和前端使用，请使用 `ClientUser` 依赖注入。

```python
from app.dependencies.user import ClientUser


@router.get("/some-api")
async def _(current_user: ClientUser):
    ...
```

此外还存在 `get_current_user_and_token` 和 `get_client_user_and_token` 变种，用来同时获得当前用户的 token。

### Service

所有的核心业务逻辑放在 `app.service` 里：

- 业务逻辑需要要以类实现
- 日志只需要使用 `app.log` 中的 `logger` 即可。服务器会对 Service 的日志进行包装。

### 定时任务/启动任务/关闭任务

均定义在 `app.tasks` 里。

- 均在 `__init__.py` 进行导出
- 对于启动任务/关闭任务，在 `main.py` 的 `lifespan` 调用。
- 定时任务使用 APScheduler

### 耗时任务

- 如果这个任务来自 API Router，请使用 FastAPI 提供的 [`BackgroundTasks`](https://fastapi.tiangolo.com/tutorial/background-tasks)
- 其他情况，使用 `app.utils` 的 `bg_tasks`，它提供了与 FastAPI 的 `BackgroundTasks` 类似的功能。

### 代码质量和代码检查

使用 `pre-commit` 在提交之前执行代码质量标准。这确保所有代码都通过 `ruff`（用于代码检查和格式化）和 `pyright`（用于类型检查）的检查。

#### 设置

要设置 `pre-commit`，请运行以下命令：

```bash
pre-commit install
```

这将安装 pre-commit 钩子，每次提交时会自动运行。如果任何检查失败，提交将被中止。您需要修复报告的问题并暂存更改，然后再尝试提交。

pre-commit 不提供 pyright 的 hook，您需要手动运行 `pyright` 检查类型错误。

### 提交信息指南

遵循 [AngularJS 提交规范](https://github.com/angular/angular.js/blob/master/DEVELOPERS.md#commit-message-format) 来编写提交信息。

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

**范围** 可以是任何指定提交更改位置的内容。例如 `api`、`db`、`auth` 等等。对整个项目的更改使用 `project`。

**主题** 包含对更改的简洁描述。

### 持续集成检查

所有提交应该通过以下 CI 检查：

- Ruff Lint
- Pyright Lint
- pre-commit

## 添加贡献者

仓库维护者需要使用 [all-contributors-bot](https://allcontributors.org/docs/en/bot/usage) 添加有效贡献者。

感谢您的贡献！
