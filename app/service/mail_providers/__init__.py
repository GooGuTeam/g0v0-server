"""Mail service providers module.

This module provides the mail provider abstraction and initialization logic,
supporting both built-in providers (SMTP) and plugin-based providers.
"""

import asyncio
import importlib

from app.config import settings

from ._base import MailServiceProvider

PROVIDER: MailServiceProvider | None = None
_init_lock = asyncio.Lock()


async def init_provider(
    provider: str | None = None,
    provider_config: dict | None = None,
    set_to_global: bool = True,
) -> MailServiceProvider | None:
    """Initialize the mail service provider.

    Dynamically imports and initializes the configured mail service provider.
    Supports built-in providers and plugin-based providers.

    Provider naming:
        - "-xxx": Load from plugin with id "xxx" (e.g., "-mailersend_provider")
        - "xxx": Built-in provider (e.g., "smtp")
        - "a.b.c": Absolute module path

    Args:
        provider: The name of the provider to initialize. If None, uses settings.email_provider.
        provider_config: Configuration dictionary for the provider. If None, uses settings.email_provider_config.
        set_to_global: If True, sets the initialized provider as the global PROVIDER.

    Returns:
        The initialized MailServiceProvider, or None if initialization fails.

    Raises:
        ImportError: If the provider module cannot be imported.
        RuntimeError: If a plugin provider is not loaded.
    """
    if provider is None:
        provider = settings.email_provider
    if provider_config is None:
        provider_config = settings.email_provider_config

    global PROVIDER
    if set_to_global and PROVIDER is not None:
        return PROVIDER

    try:
        if provider.startswith("-"):
            from app.plugins import manager

            # Provider is from a plugin, e.g. "-mailersend_provider"
            plugin_id = provider[1:]
            plugin = manager.get_plugin_by_id(plugin_id)
            if plugin is None:
                raise ImportError(f"Plugin '{plugin_id}' not found for mail service provider")
            module = plugin.module
            if module is None:
                raise RuntimeError(f"Plugin '{plugin_id}' is not loaded.")
        elif "." not in provider:
            # Built-in provider, e.g. "smtp"
            module = importlib.import_module(f".{provider}", package="app.service.mail_providers")
        else:
            # Absolute package path, e.g. "plugins.my_mail_provider"
            module = importlib.import_module(provider)

        # Get the provider class - convention: module exports MailServiceProvider
        provider_class = getattr(module, "MailServiceProvider", None)
        if provider_class is None:
            raise ImportError(f"Module '{provider}' does not export 'MailServiceProvider' class")

        provider_instance = provider_class(**provider_config)
        if provider_instance is not None:
            await provider_instance.init()
            if set_to_global:
                async with _init_lock:
                    PROVIDER = provider_instance
            else:
                return provider_instance

    except (ImportError, AttributeError) as e:
        raise ImportError(f"Failed to import mail service provider '{provider}'") from e

    return PROVIDER


def get_provider() -> MailServiceProvider:
    """Get the global mail service provider instance.

    Returns:
        The initialized MailServiceProvider.

    Raises:
        RuntimeError: If the provider has not been initialized.
    """
    if PROVIDER is None:
        raise RuntimeError("Mail service provider not initialized. Call init_provider() first.")
    return PROVIDER


__all__ = [
    "PROVIDER",
    "MailServiceProvider",
    "get_provider",
    "init_provider",
]
