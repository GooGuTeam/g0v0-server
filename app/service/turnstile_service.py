"""Cloudflare Turnstile verification service.

Responsible for verifying the validity of Cloudflare Turnstile tokens.
"""

from app.config import settings
from app.log import log

import httpx

logger = log("Turnstile")

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"  # noqa: S105


class TurnstileService:
    """Cloudflare Turnstile verification service."""

    @staticmethod
    async def verify_token(token: str, remoteip: str | None = None) -> tuple[bool, str]:
        """Verify Turnstile token.

        Args:
            token: Turnstile response token.
            remoteip: Client IP address (optional).

        Returns:
            tuple[bool, str]: (success, error_message)
        """
        # If Turnstile verification disabled, return success directly
        if not settings.enable_turnstile_verification:
            return True, ""

        # Dev mode: skip verification
        if settings.turnstile_dev_mode:
            logger.debug("Turnstile dev mode enabled, skipping verification")
            return True, ""

        # Check if dummy token (only accept in dev mode)
        if token == DUMMY_TOKEN:
            logger.warning(f"Dummy token provided but dev mode is disabled (IP: {remoteip})")
            return False, "Invalid verification token"

        # Check configuration
        if not settings.turnstile_secret_key:
            logger.error("Turnstile secret key not configured")
            return False, "Turnstile verification not configured"

        # Prepare request data
        data = {"secret": settings.turnstile_secret_key, "response": token}

        if remoteip:
            data["remoteip"] = remoteip

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(TURNSTILE_VERIFY_URL, data=data)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    logger.debug(f"Turnstile verification successful for IP {remoteip}")
                    return True, ""
                else:
                    error_codes = result.get("error-codes", [])
                    logger.warning(f"Turnstile verification failed for IP {remoteip}, errors: {error_codes}")

                    # Provide friendly error message based on error code
                    if "timeout-or-duplicate" in error_codes:
                        return False, "Verification token expired or already used"
                    elif "invalid-input-response" in error_codes:
                        return False, "Invalid verification token"
                    elif "missing-input-response" in error_codes:
                        return False, "Verification token is required"
                    else:
                        return False, "Verification failed"

        except httpx.TimeoutException:
            logger.error("Turnstile verification timeout")
            return False, "Verification service timeout"
        except httpx.HTTPError as e:
            logger.error(f"Turnstile verification HTTP error: {e}")
            return False, "Verification service error"
        except Exception as e:  # Catch any unexpected errors
            logger.exception(f"Turnstile verification unexpected error: {e}")
            return False, "Verification service error"


turnstile_service = TurnstileService()
