"""Custom exceptions and a uniform HTTP-error formatter."""

from __future__ import annotations

import httpx


class GradingMCPError(Exception):
    """Base class for all tcg_mcp errors."""


class ProviderNotEnabledError(GradingMCPError):
    """Raised when a tool requests a provider that has no credentials configured."""


class NotSupportedError(GradingMCPError):
    """Raised when a provider does not implement an optional capability."""


class CertNotFoundError(GradingMCPError):
    """Raised when a cert lookup succeeds but no record is found."""


def format_http_error(e: Exception, *, provider: str) -> str:
    """Translate an httpx exception into an actionable, agent-friendly string.

    Args:
        e: The exception raised during an HTTP call.
        provider: Provider name (e.g. "psa") for context in the message.

    Returns:
        A human-readable error message that suggests next steps.
    """
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return (
                f"Error: {provider.upper()} API token is invalid or expired. "
                "For PSA: regenerate at https://www.psacard.com/publicapi"
            )
        if status == 403:
            return (
                f"Error: {provider.upper()} API rejected the request (403 Forbidden). "
                "Your token may not be authorized for this endpoint."
            )
        if status == 404:
            return f"Error: {provider.upper()} returned 404 — resource not found."
        if status == 429:
            return (
                f"Error: {provider.upper()} API rate limit exceeded. "
                "Wait and retry, or upgrade your plan."
            )
        if 500 <= status < 600:
            return (
                f"Error: {provider.upper()} API returned a server error ({status}). "
                "This is upstream — try again shortly."
            )
        return f"Error: {provider.upper()} API returned status {status}."

    if isinstance(e, httpx.TimeoutException):
        return f"Error: {provider.upper()} request timed out. Try again."

    if isinstance(e, httpx.RequestError):
        return f"Error: network failure talking to {provider.upper()}: {type(e).__name__}"

    return f"Error: unexpected {type(e).__name__} from {provider.upper()} provider"
