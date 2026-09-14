"""Provider-agnostic LLM access layer (spec section 5)."""

from backend.services.llm.base import (
    AuthError,
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    ModelNotFoundError,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RateLimitError,
    Role,
    Usage,
)
from backend.services.llm.ratelimit import DailyQuotaExhausted, RateLimit, RateLimiter
from backend.services.llm.registry import (
    CHANNEL_ROLES,
    PROVIDER_SPECS,
    ChannelBinding,
    available_providers,
    build_provider,
    channels_are_independent,
    resolve_channel,
)

from .settings import LLMSettings, llm_settings
__all__ = [
    "LLMSettings",
    "llm_settings",
    "AuthError", "LLMError", "LLMProvider", "LLMResponse", "Message",
    "ModelNotFoundError", "ProviderUnavailableError", "QuotaExhaustedError",
    "RateLimitError", "Role",
    "Usage", "DailyQuotaExhausted", "RateLimit", "RateLimiter", "CHANNEL_ROLES",
    "PROVIDER_SPECS", "ChannelBinding", "available_providers", "build_provider",
    "channels_are_independent", "resolve_channel",
]
