from contextlib import contextmanager
import json
from pathlib import Path

from g0v0_migrations.model import ContextObject, G0v0ServerDatabaseConfig
from g0v0_migrations.utils import detect_g0v0_server_path

import alembic.command
from alembic.config import Config as AlembicConfig
import click


def _ensure_migrations_path(obj: ContextObject) -> str:
    migrations_path = obj["alembic_config"].get_section_option(
        obj["alembic_config"].config_ini_section, "script_location"
    )
    if migrations_path is None:
        raise click.ClickException("Could not determine script_location from alembic config.")
    if not Path(migrations_path).exists():
        Path(migrations_path).mkdir(parents=True, exist_ok=True)
        Path(migrations_path).joinpath("versions").mkdir(parents=True, exist_ok=True)
    return migrations_path


@contextmanager
def _ensure_env(obj: ContextObject, autogenerate: bool = False):
    migrations_path = _ensure_migrations_path(obj)
    for file in Path(__file__).parent.joinpath("templates").iterdir():
        if file.is_file():
            dest_file = Path(migrations_path).joinpath(file.name)
            if dest_file.exists():
                continue
            txt = file.read_text()
            if file.name == "env.py":
                txt = txt.replace("<name_placeholder>", obj["plugin_id"] or "")
                plugin_path = obj["plugin_path"]
                if obj["plugin_id"] and plugin_path and autogenerate:
                    plugin_import_name = plugin_path.name.replace("-", "_")
                    txt = txt.replace(
                        "# <import_placeholder>",
                        (
                            f"import sys; "
                            f"sys.path.insert(0, r'{plugin_path.parent.as_posix()}'); "
                            f"sys.path.insert(0, r'{obj['g0v0_server_path'].as_posix()}'); "
                            f"from {plugin_import_name} import *"
                        ),
                    )
            dest_file.write_text(txt)
    db_config = G0v0ServerDatabaseConfig(_env_file=obj["g0v0_server_path"] / ".env")  # pyright: ignore[reportCallIssue]
    alembic_config = obj["alembic_config"]
    original_url = obj["alembic_config"].get_section_option(alembic_config.config_ini_section, "sqlalchemy.url")
    alembic_config.set_section_option(alembic_config.config_ini_section, "sqlalchemy.url", db_config.database_url)
    try:
        yield
    finally:
        if obj["plugin_id"]:
            for file in Path(__file__).parent.joinpath("templates").iterdir():
                if file.is_file():
                    dest_file = Path(migrations_path).joinpath(file.name)
                    if dest_file.exists():
                        dest_file.unlink()
        alembic_config.set_section_option(
            alembic_config.config_ini_section,
            "sqlalchemy.url",
            original_url or "",
        )


@click.group()
@click.option(
    "-c",
    "--config",
    default=None,
    help="Path to config file of Alembic. ",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-p",
    "--path",
    "g0v0_server_path",
    default=None,
    help=(
        "The directory path to g0v0-server. If not provided, "
        "the current working directory and its parents will be searched."
    ),
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "-n",
    "--name",
    default="alembic",
    show_default=True,
    help="Name of the migration. See https://alembic.sqlalchemy.org/en/latest/cookbook.html#multiple-environments",
)
@click.option(
    "-P",
    "--plugin-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the plugin directory.",
)
@click.pass_context
def g0v0_migrate(
    ctx: click.Context,
    config: Path | None,
    name: str,
    g0v0_server_path: Path | None,
    plugin_path: Path | None,
):
    config_path: Path | None = config
    if g0v0_server_path is None:
        g0v0_server_path = detect_g0v0_server_path()
        if g0v0_server_path is None:
            raise click.ClickException("Could not detect g0v0-server path. Please provide it via --path option.")
        click.echo(f"Detected g0v0-server at {g0v0_server_path}")
    if config_path is None:
        config_path = g0v0_server_path / "alembic.ini"
        if not config_path.exists():
            raise click.ClickException(
                f"Could not find alembic.ini at {config_path}. Please provide it via --config option."
            )
    alembic_config = AlembicConfig(config_path.as_posix())
    alembic_config.config_ini_section = name

    # detect cwd is a plugin.
    plugin_id: str | None = None
    cwd = (plugin_path or Path.cwd()).resolve()
    if cwd.joinpath("plugin.json").exists():
        meta = json.loads(cwd.joinpath("plugin.json").read_text())
        plugin_id = meta.get("id")
        if plugin_id is None:
            raise click.ClickException(f"Could not detect plugin id from {cwd / 'plugin.json'}.")
        click.echo(f"Detected plugin {plugin_id} at {cwd}")

        alembic_config.set_section_option(
            alembic_config.config_ini_section, "script_location", cwd.joinpath("migrations").as_posix()
        )

    obj: ContextObject = {
        "g0v0_server_path": g0v0_server_path,
        "alembic_config": alembic_config,
        "plugin_path": cwd,
        "plugin_id": plugin_id,
    }
    ctx.obj = obj


@g0v0_migrate.command()
@click.option(
    "-r",
    "--rev-range",
    default=None,
    help="Specify a revision range; format is [start]:[end].",
)
@click.pass_context
def history(ctx: click.Context, rev_range: str | None):
    """List changeset scripts in chronological order."""
    alembic.command.history(ctx.obj["alembic_config"], rev_range=rev_range)


@g0v0_migrate.command()
@click.option(
    "-m",
    "--message",
    default="",
    help="Message string to use with the revision.",
)
@click.option(
    "--autogenerate",
    default=False,
    help="Populate revision script with candidate migration operations, based on comparison of database to model.",
    is_flag=True,
)
@click.option(
    "--sql",
    default=False,
    help="Don't emit SQL to database - dump to standard output/file instead. See https://alembic.sqlalchemy.org/en/latest/offline.html",
    is_flag=True,
)
@click.option("--head", default="head", help="Specify head revision or <branchname>@head to base new revision on.")
@click.option("--splice", is_flag=True, help="Allow a non-head revision as the 'head' to splice onto.")
@click.option("--branch-label", default=None, help="Specify a branch label to apply to the new revision.")
@click.option("--version-path", default=None, help="Specify a version path to place the new revision file in.")
@click.option("--rev-id", default=None, help="Specify a hardcoded revision id instead of generating one.")
@click.option("--depends-on", default=None, help="Specify one or more revisions that this revision depends on.")
@click.pass_context
def revision(
    ctx: click.Context,
    message: str,
    autogenerate: bool,
    sql: bool,
    head: str,
    splice: bool,
    branch_label: str | None,
    version_path: str | None,
    rev_id: str | None,
    depends_on: str | None,
):
    """Create a new revision file."""
    obj: ContextObject = ctx.obj

    with _ensure_env(obj, autogenerate=autogenerate):
        alembic.command.revision(
            obj["alembic_config"],
            message=message,
            autogenerate=autogenerate,
            sql=sql,
            head=head,
            splice=splice,
            branch_label=branch_label,
            version_path=version_path,
            rev_id=rev_id,
            depends_on=depends_on,
        )


@g0v0_migrate.command()
@click.argument(
    "revision",
)
@click.option(
    "--sql",
    is_flag=True,
    help="Don't emit SQL to database - dump to standard output/file instead. See https://alembic.sqlalchemy.org/en/latest/offline.html",
)
@click.pass_context
def upgrade(ctx: click.Context, revision: str, sql: bool):
    """Upgrade to a later version."""
    obj: ContextObject = ctx.obj

    with _ensure_env(ctx.obj):
        alembic.command.upgrade(obj["alembic_config"], revision, sql=sql)


@g0v0_migrate.command()
@click.argument(
    "revision",
)
@click.option(
    "--sql",
    is_flag=True,
    help="Don't emit SQL to database - dump to standard output/file instead. See https://alembic.sqlalchemy.org/en/latest/offline.html",
)
@click.pass_context
def downgrade(ctx: click.Context, revision: str, sql: bool):
    """Downgrade to an earlier version."""
    obj: ContextObject = ctx.obj

    with _ensure_env(ctx.obj):
        alembic.command.downgrade(obj["alembic_config"], revision, sql=sql)


@g0v0_migrate.command()
@click.pass_context
def current(ctx: click.Context):
    """Display the current revision for each database."""
    obj: ContextObject = ctx.obj

    with _ensure_env(ctx.obj):
        alembic.command.current(obj["alembic_config"])


@g0v0_migrate.command()
@click.option("-v", "--verbose", is_flag=True, help="Use more verbose output.")
@click.pass_context
def branches(ctx: click.Context, verbose: bool):
    """Show current branch points."""
    alembic.command.branches(ctx.obj["alembic_config"], verbose=verbose)


@g0v0_migrate.command()
@click.pass_context
def check(ctx: click.Context):
    """Check if there are any new operations to be generated."""
    obj: ContextObject = ctx.obj
    with _ensure_env(obj):
        alembic.command.check(obj["alembic_config"])


@g0v0_migrate.command()
@click.argument("revision")
@click.pass_context
def edit(ctx: click.Context, revision: str):
    """Edit revision script(s) using $EDITOR."""
    alembic.command.edit(ctx.obj["alembic_config"], revision)


@g0v0_migrate.command()
@click.option(
    "--sql",
    is_flag=True,
    help="Don't emit SQL to database - dump to standard output/file instead. See https://alembic.sqlalchemy.org/en/latest/offline.html",
)
@click.pass_context
def ensure_version(ctx: click.Context, sql: bool):
    """Create the alembic version table if it doesn't exist already."""
    obj: ContextObject = ctx.obj
    with _ensure_env(obj):
        alembic.command.ensure_version(obj["alembic_config"], sql=sql)


@g0v0_migrate.command()
@click.option("-v", "--verbose", is_flag=True, help="Use more verbose output.")
@click.option(
    "--resolve-dependencies",
    is_flag=True,
    help="Treat dependency versions as down revisions.",
)
@click.pass_context
def heads(ctx: click.Context, verbose: bool, resolve_dependencies: bool):
    """Show current available heads in the script directory."""
    alembic.command.heads(
        ctx.obj["alembic_config"],
        verbose=verbose,
        resolve_dependencies=resolve_dependencies,
    )


@g0v0_migrate.command()
@click.pass_context
def list_templates(ctx: click.Context):
    """List available templates."""
    alembic.command.list_templates(ctx.obj["alembic_config"])


@g0v0_migrate.command()
@click.argument("revisions", nargs=-1)
@click.option("-m", "--message", default=None, help="Message string to use with the revision.")
@click.option("--branch-label", default=None, help="Specify a branch label to apply to the new revision.")
@click.option("--rev-id", default=None, help="Specify a hardcoded revision id instead of generating one.")
@click.pass_context
def merge(
    ctx: click.Context,
    revisions: tuple[str, ...],
    message: str | None,
    branch_label: str | None,
    rev_id: str | None,
):
    """Merge two revisions together. Creates a new migration file."""
    obj: ContextObject = ctx.obj
    with _ensure_env(obj):
        alembic.command.merge(
            obj["alembic_config"],
            revisions=revisions,
            message=message,
            branch_label=branch_label,
            rev_id=rev_id,
        )


@g0v0_migrate.command()
@click.argument("revision")
@click.pass_context
def show(ctx: click.Context, revision: str):
    """Show the revision(s) denoted by the given symbol."""
    alembic.command.show(ctx.obj["alembic_config"], revision)


@g0v0_migrate.command()
@click.argument("revision")
@click.option(
    "--sql",
    is_flag=True,
    help="Don't emit SQL to database - dump to standard output/file instead. See https://alembic.sqlalchemy.org/en/latest/offline.html",
)
@click.option("--tag", default=None, help="Arbitrary 'tag' name - can be used by custom env.py scripts.")
@click.option("--purge", is_flag=True, help="Unconditionally erase the version table before stamping.")
@click.pass_context
def stamp(ctx: click.Context, revision: str, sql: bool, tag: str | None, purge: bool):
    """'stamp' the revision table with the given revision; don't run any migrations."""
    obj: ContextObject = ctx.obj
    with _ensure_env(obj):
        alembic.command.stamp(obj["alembic_config"], revision, sql=sql, tag=tag, purge=purge)


@g0v0_migrate.command()
@click.pass_context
def upgrade_all(ctx: click.Context):
    """Upgrade the g0v0-server and all plugins' databases to the latest version."""
    obj: ContextObject = ctx.obj

    if obj["g0v0_server_path"] != Path.cwd().resolve():
        raise click.ClickException("Please run this command from the g0v0-server root directory.")

    # Upgrade g0v0-server
    click.echo("Upgrading g0v0-server...")
    with _ensure_env(obj):
        alembic.command.upgrade(obj["alembic_config"], "head")
    # Upgrade plugins
    plugins_path = obj["g0v0_server_path"].joinpath("plugins")
    if not plugins_path.exists():
        click.echo("No plugins directory found, skipping plugin upgrades.")
        return
    for plugin_dir in plugins_path.iterdir():
        if not plugin_dir.is_dir():
            continue
        if not (plugin_meta_path := plugin_dir.joinpath("plugin.json")).exists():
            continue
        plugin_meta = json.loads(plugin_meta_path.read_text(encoding="utf-8"))
        plugin_id = plugin_meta.get("id")
        if plugin_id is None:
            click.echo(f"Could not detect plugin id from {plugin_meta_path}, skipping...")
            continue
        click.echo(f"Upgrading plugin {plugin_id}...")
        alembic_config = obj["alembic_config"]
        alembic_config.set_section_option(
            alembic_config.config_ini_section, "script_location", plugin_dir.joinpath("migrations").as_posix()
        )
        with _ensure_env(
            {
                **obj,
                "plugin_id": plugin_id,
            }
        ):
            alembic.command.upgrade(obj["alembic_config"], "head")


if __name__ == "__main__":
    g0v0_migrate()
