import asyncio  # noqa: INP001
from logging.config import fileConfig

# <import_placeholder>
from alembic import context
from alembic.autogenerate import rewriter
from alembic.operations import ops
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = SQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

PLUGIN_NAME = "<name_placeholder>"
ALEMBIC_VERSION_TABLE_NAME = f"{PLUGIN_NAME}_alembic_version" if PLUGIN_NAME else "alembic_version"
writer = rewriter.Rewriter()


def is_plugin_prefix(name: str) -> bool:
    return bool(PLUGIN_NAME) and name.startswith(f"plugin_{PLUGIN_NAME}_")


def process_revision_directives(context, revision, directives):  # noqa: ARG001
    script = directives[0]
    if script.upgrade_ops.is_empty():
        directives[:] = []
    for op in [*script.upgrade_ops.ops, *script.downgrade_ops.ops]:
        if isinstance(op, ops.RenameTableOp):
            old_name = op.table_name
            new_name = op.new_table_name
            if not is_plugin_prefix(old_name):
                op.table_name = f"plugin_{PLUGIN_NAME}_{old_name}"
            if not is_plugin_prefix(new_name):
                op.new_table_name = f"plugin_{PLUGIN_NAME}_{new_name}"
        else:
            table_name = getattr(op, "table_name", None)
            if table_name and not is_plugin_prefix(table_name):
                setattr(op, "table_name", f"plugin_{PLUGIN_NAME}_{table_name}")


def include_object(object, name, type_, reflected, compare_to) -> bool:  # noqa: ARG001
    if type_ != "table":
        return True
    return not (name.endswith("alembic_version") and name != ALEMBIC_VERSION_TABLE_NAME)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
        version_table=ALEMBIC_VERSION_TABLE_NAME,
        process_revision_directives=process_revision_directives,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        version_table=ALEMBIC_VERSION_TABLE_NAME,
        process_revision_directives=process_revision_directives,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    sa_config = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        sa_config,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
