"""Dependency injection container for service management."""

import asyncio
import logging
from enum import Enum, auto
from typing import TypeVar, Type, Callable, Any, overload

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Scope(Enum):
    """Service lifecycle scope."""
    SINGLETON = auto()  # Single instance for entire application
    TRANSIENT = auto()  # New instance on each resolve


class ServiceContainer:
    """
    Simple dependency injection container for managing service instances.

    Features:
    - Singleton service registration
    - Lazy initialization
    - Dependency resolution
    - Lifecycle management
    """

    _instance: "ServiceContainer | None" = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._factories: dict[str, Callable[..., Any]] = {}
        self._type_registry: dict[Type, tuple[Callable[..., Any], Scope]] = {}
        self._initialized = False

    @classmethod
    async def get_instance(cls) -> "ServiceContainer":
        """Get singleton instance of ServiceContainer."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = ServiceContainer()
        return cls._instance

    @classmethod
    def get_sync(cls) -> "ServiceContainer":
        """Get container instance synchronously (use only if already initialized)."""
        if cls._instance is None:
            cls._instance = ServiceContainer()
        return cls._instance

    def register(self, name: str, factory: Callable[..., T]) -> None:
        """
        Register a service factory.

        Args:
            name: Service name/identifier
            factory: Factory function to create the service
        """
        self._factories[name] = factory
        logger.debug(f"Registered service factory: {name}")

    def register_instance(self, name: str, instance: Any) -> None:
        """
        Register an existing service instance.

        Args:
            name: Service name/identifier
            instance: Pre-created service instance
        """
        self._services[name] = instance
        logger.debug(f"Registered service instance: {name}")

    def get(self, name: str) -> Any:
        """
        Get a service instance by name.

        Args:
            name: Service name/identifier

        Returns:
            Service instance

        Raises:
            KeyError: If service is not registered
        """
        # Return cached instance if exists
        if name in self._services:
            return self._services[name]

        # Create from factory
        if name in self._factories:
            instance = self._factories[name]()
            self._services[name] = instance
            logger.debug(f"Created service instance: {name}")
            return instance

        raise KeyError(f"Service not registered: {name}")

    async def get_async(self, name: str) -> Any:
        """
        Get a service instance asynchronously.

        Use this for services that require async initialization.
        """
        # Return cached instance if exists
        if name in self._services:
            return self._services[name]

        # Create from factory
        if name in self._factories:
            factory = self._factories[name]
            if asyncio.iscoroutinefunction(factory):
                instance = await factory()
            else:
                instance = factory()
            self._services[name] = instance
            logger.debug(f"Created async service instance: {name}")
            return instance

        raise KeyError(f"Service not registered: {name}")

    def has(self, name: str) -> bool:
        """Check if a service is registered."""
        return name in self._services or name in self._factories

    def register_type(
        self,
        service_type: Type[T],
        factory: Callable[..., T],
        scope: Scope = Scope.SINGLETON
    ) -> None:
        """
        Register a service by type with specified scope.

        Args:
            service_type: The type/class to register
            factory: Factory function to create the service
            scope: Lifecycle scope (SINGLETON or TRANSIENT)
        """
        self._type_registry[service_type] = (factory, scope)
        logger.debug(f"Registered type: {service_type.__name__} ({scope.name})")

    def resolve(self, service_type: Type[T]) -> T:
        """
        Resolve a service by type with full type safety.

        Args:
            service_type: The type/class to resolve

        Returns:
            Instance of the requested type

        Raises:
            KeyError: If type is not registered
        """
        if service_type not in self._type_registry:
            raise KeyError(f"Type not registered: {service_type.__name__}")

        factory, scope = self._type_registry[service_type]

        if scope == Scope.SINGLETON:
            # Check cache first
            if service_type in self._services:
                return self._services[service_type]
            # Create and cache
            instance = factory()
            self._services[service_type] = instance
            return instance
        else:
            # TRANSIENT: always create new
            return factory()

    def clear(self) -> None:
        """Clear all registered services."""
        self._services.clear()
        self._factories.clear()
        self._type_registry.clear()


# Service name constants
class Services:
    """Service name constants for type-safe access."""

    CONFIG = "config_service"
    TMDB = "tmdb_service"
    PARSER = "parser_service"
    NFO = "nfo_service"
    RENAME = "rename_service"
    IMAGE = "image_service"
    SUBTITLE = "subtitle_service"
    EMBY = "emby_service"
    SCRAPER = "scraper_service"
    WATCHER = "watcher_service"
    AUTH = "auth_service"
    SESSION = "session_service"
    HISTORY = "history_service"
    FILE = "file_service"
    P115 = "p115_service"
    SCRAPED_FILE = "scraped_file_service"
    MANUAL_JOB = "manual_job_service"
    SCRAPE_JOB = "scrape_job_service"
    SCHEDULER = "scheduler_service"
    TEMPLATE = "template_service"
    FINGERPRINT = "fingerprint_service"
    WEBSOCKET = "websocket_manager"
    LOG = "log_service"


def get_container() -> ServiceContainer:
    """Get the service container instance (sync)."""
    return ServiceContainer.get_sync()


async def get_container_async() -> ServiceContainer:
    """Get the service container instance (async)."""
    return await ServiceContainer.get_instance()


async def init_services() -> None:
    """
    Initialize all application services.

    Call this at application startup to register all services.
    """
    container = await get_container_async()

    # Import services here to avoid circular imports
    from server.services.config_service import ConfigService
    from server.services.parser_service import ParserService
    from server.services.nfo_service import NFOService
    from server.services.rename_service import RenameService
    from server.services.subtitle_service import SubtitleService
    from server.services.template_service import TemplateService
    from server.services.websocket_manager import ConnectionManager

    # Register core services (stateless, can be singletons)
    container.register(Services.CONFIG, ConfigService)
    container.register(Services.PARSER, ParserService)
    container.register(Services.NFO, NFOService)
    container.register(Services.RENAME, RenameService)
    container.register(Services.SUBTITLE, SubtitleService)
    container.register(Services.TEMPLATE, TemplateService)

    # Register WebSocket manager as singleton instance
    container.register_instance(Services.WEBSOCKET, ConnectionManager())

    # Services with dependencies (IMAGE, TMDB, EMBY, SCRAPER) will be created lazily
    # via their respective get_*_service() functions
    logger.info("Service container initialized")


async def cleanup_services() -> None:
    """
    Cleanup all services.

    Call this at application shutdown.
    """
    container = await get_container_async()
    container.clear()
    ServiceContainer._instance = None
    logger.info("Service container cleaned up")


# =============================================================================
# Generic Service Factory (DRY principle)
# =============================================================================


# Service registry for automatic resolution
_SERVICE_REGISTRY: dict[str, tuple[str, str]] = {
    Services.CONFIG: ("server.services.config_service", "ConfigService"),
    Services.PARSER: ("server.services.parser_service", "ParserService"),
    Services.NFO: ("server.services.nfo_service", "NFOService"),
    Services.RENAME: ("server.services.rename_service", "RenameService"),
    Services.IMAGE: ("server.services.image_service", "ImageService"),
    Services.SUBTITLE: ("server.services.subtitle_service", "SubtitleService"),
    Services.TEMPLATE: ("server.services.template_service", "TemplateService"),
    Services.FILE: ("server.services.file_service", "FileService"),
    Services.HISTORY: ("server.services.history_service", "HistoryService"),
    Services.SCHEDULER: ("server.services.scheduler_service", "SchedulerService"),
    Services.MANUAL_JOB: ("server.services.manual_job_service", "ManualJobService"),
    Services.SCRAPE_JOB: ("server.services.scrape_job_service", "ScrapeJobService"),
    Services.SCRAPED_FILE: ("server.services.scraped_file_service", "ScrapedFileService"),
    Services.FINGERPRINT: ("server.services.fingerprint_service", "FingerprintService"),
    Services.SCRAPER: ("server.services.scraper_service", "ScraperService"),
    Services.WEBSOCKET: ("server.services.websocket_manager", "ConnectionManager"),
    Services.WATCHER: ("server.services.watcher_service", "WatcherService"),
    Services.LOG: ("server.services.log_service", "LogService"),
}


def get_service(service_name: str) -> Any:
    """
    Unified service resolver - replaces all get_xxx_service functions.

    Args:
        service_name: Service name constant from Services class.

    Returns:
        Service instance (singleton).
    """
    import importlib
    container = get_container()

    if container.has(service_name):
        return container.get(service_name)

    if service_name == Services.P115:
        return get_p115_service()

    if service_name not in _SERVICE_REGISTRY:
        raise KeyError(f"Unknown service: {service_name}")

    module_path, class_name = _SERVICE_REGISTRY[service_name]
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    container.register(service_name, cls)
    return container.get(service_name)


def _get_simple_service(service_name: str, service_module: str, service_class: str) -> Any:
    """
    Generic factory for simple services without dependencies.

    Args:
        service_name: Service name constant from Services class.
        service_module: Module path (e.g., 'server.services.config_service').
        service_class: Class name (e.g., 'ConfigService').

    Returns:
        Service instance (singleton).
    """
    import importlib
    container = get_container()
    if not container.has(service_name):
        module = importlib.import_module(service_module)
        cls = getattr(module, service_class)
        container.register(service_name, cls)
    return container.get(service_name)


def _get_singleton_service(service_name: str, service_module: str, service_class: str) -> Any:
    """
    Generic factory for singleton services that should be instantiated immediately.

    Args:
        service_name: Service name constant from Services class.
        service_module: Module path.
        service_class: Class name.

    Returns:
        Service instance (singleton).
    """
    import importlib
    container = get_container()
    if not container.has(service_name):
        module = importlib.import_module(service_module)
        cls = getattr(module, service_class)
        container.register_instance(service_name, cls())
    return container.get(service_name)


# =============================================================================
# FastAPI Dependency Functions - Simple Services
# =============================================================================


def get_config_service():
    """FastAPI dependency for ConfigService."""
    return _get_simple_service(
        Services.CONFIG,
        "server.services.config_service",
        "ConfigService"
    )


def get_parser_service():
    """FastAPI dependency for ParserService."""
    return _get_simple_service(
        Services.PARSER,
        "server.services.parser_service",
        "ParserService"
    )


def get_nfo_service():
    """FastAPI dependency for NFOService."""
    return _get_simple_service(
        Services.NFO,
        "server.services.nfo_service",
        "NFOService"
    )


def get_rename_service():
    """FastAPI dependency for RenameService."""
    return _get_simple_service(
        Services.RENAME,
        "server.services.rename_service",
        "RenameService"
    )


def get_image_service():
    """FastAPI dependency for ImageService."""
    from server.services.image_service import ImageService
    container = get_container()
    if not container.has(Services.IMAGE):
        config_service = get_config_service()
        container.register_instance(
            Services.IMAGE,
            ImageService(config_service=config_service)
        )
    return container.get(Services.IMAGE)


def get_subtitle_service():
    """FastAPI dependency for SubtitleService."""
    return _get_simple_service(
        Services.SUBTITLE,
        "server.services.subtitle_service",
        "SubtitleService"
    )


def get_template_service():
    """FastAPI dependency for TemplateService."""
    return _get_simple_service(
        Services.TEMPLATE,
        "server.services.template_service",
        "TemplateService"
    )


def get_file_service():
    """FastAPI dependency for FileService."""
    return _get_simple_service(
        Services.FILE,
        "server.services.file_service",
        "FileService"
    )


def get_p115_service():
    """FastAPI dependency for P115Service."""
    from server.services.p115_service import P115Service

    container = get_container()
    if not container.has(Services.P115):
        container.register_instance(
            Services.P115,
            P115Service(config_service=get_config_service())
        )
    return container.get(Services.P115)


def get_history_service():
    """FastAPI dependency for HistoryService."""
    return _get_simple_service(
        Services.HISTORY,
        "server.services.history_service",
        "HistoryService"
    )


def get_scheduler_service():
    """FastAPI dependency for SchedulerService."""
    return _get_simple_service(
        Services.SCHEDULER,
        "server.services.scheduler_service",
        "SchedulerService"
    )


def get_manual_job_service():
    """FastAPI dependency for ManualJobService."""
    return _get_simple_service(
        Services.MANUAL_JOB,
        "server.services.manual_job_service",
        "ManualJobService"
    )


def get_scrape_job_service():
    """FastAPI dependency for ScrapeJobService."""
    return _get_simple_service(
        Services.SCRAPE_JOB,
        "server.services.scrape_job_service",
        "ScrapeJobService"
    )


def get_scraped_file_service():
    """FastAPI dependency for ScrapedFileService."""
    return _get_simple_service(
        Services.SCRAPED_FILE,
        "server.services.scraped_file_service",
        "ScrapedFileService"
    )


def get_fingerprint_service():
    """FastAPI dependency for FingerprintService."""
    return _get_simple_service(
        Services.FINGERPRINT,
        "server.services.fingerprint_service",
        "FingerprintService"
    )


def get_scraper_service():
    """FastAPI dependency for ScraperService."""
    from server.services.scraper_service import ScraperService
    container = get_container()
    if not container.has(Services.SCRAPER):
        container.register_instance(
            Services.SCRAPER,
            ScraperService(
                config_service=get_config_service(),
                tmdb_service=get_tmdb_service(),
                parser_service=get_parser_service(),
                nfo_service=get_nfo_service(),
                rename_service=get_rename_service(),
                image_service=get_image_service(),
                subtitle_service=get_subtitle_service(),
                emby_service=get_emby_service(),
            )
        )
    return container.get(Services.SCRAPER)


# =============================================================================
# FastAPI Dependency Functions - Singleton Services
# =============================================================================


def get_websocket_manager():
    """FastAPI dependency for ConnectionManager."""
    return _get_singleton_service(
        Services.WEBSOCKET,
        "server.services.websocket_manager",
        "ConnectionManager"
    )


def get_watcher_service():
    """FastAPI dependency for WatcherService."""
    return _get_singleton_service(
        Services.WATCHER,
        "server.services.watcher_service",
        "WatcherService"
    )


# =============================================================================
# FastAPI Dependency Functions - Services with Dependencies
# =============================================================================


def get_tmdb_service():
    """FastAPI dependency for TMDBService."""
    from server.services.tmdb_service import TMDBService
    container = get_container()
    if not container.has(Services.TMDB):
        config_service = get_config_service()
        container.register_instance(
            Services.TMDB,
            TMDBService(config_service=config_service)
        )
    return container.get(Services.TMDB)


def get_emby_service():
    """FastAPI dependency for EmbyService."""
    from server.services.emby_service import EmbyService
    container = get_container()
    if not container.has(Services.EMBY):
        config_service = get_config_service()
        container.register_instance(
            Services.EMBY,
            EmbyService(config_service=config_service)
        )
    return container.get(Services.EMBY)


def get_auth_config_service():
    """FastAPI dependency for AuthConfigService."""
    from server.services.auth_config_service import AuthConfigService
    return AuthConfigService.get_sync()


def get_log_service():
    """FastAPI dependency for LogService."""
    return _get_singleton_service(
        Services.LOG,
        "server.services.log_service",
        "LogService"
    )
