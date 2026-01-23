from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import HTTPException


class ErrorType(Enum):
    """
    All possible error types that could be passed to the client.

    Each entry is a tuple of the message key, status code and fallback message,
    which should match the arguments of ErrorEntry.

    Remember to keep this enum in sync with frontend l10n implementation.
    """

    # Default / Undisclosed
    UNKNOWN = ("unknown", 500, "Something bad has happened, please try again later.")
    INTERNAL = ("internal_error", 500, "Something has gone wrong on our side, please try again later.")

    # Auth & tokens
    NOT_AUTHENTICATED = ("not_authenticated", 401, "Not authenticated")
    INVALID_OR_EXPIRED_TOKEN = ("invalid_or_expired_token", 401, "Invalid or expired token")
    INSUFFICIENT_SCOPE = ("insufficient_scope", 403, "Insufficient scope")
    USER_NOT_VERIFIED = ("user_not_verified", 401, "User not verified")
    MISSING_API_KEY = ("missing_api_key", 401, "Missing API key")
    INVALID_API_KEY = ("invalid_api_key", 401, "Invalid API key")

    # General restriction
    ACCOUNT_RESTRICTED = ("account_restricted", 403, "Your account is restricted and cannot perform this action.")
    MESSAGING_RESTRICTED = ("messaging_restricted", 403, "You are restricted from sending messages.")

    # Not found
    NOT_FOUND = ("not_found", 404, "Not found")
    USER_NOT_FOUND = ("user_not_found", 404, "User not found")
    TARGET_USER_NOT_FOUND = ("target_user_not_found", 404, "Target user not found")
    BEATMAP_NOT_FOUND = ("beatmap_not_found", 404, "Beatmap not found")
    BEATMAPSET_NOT_FOUND = ("beatmapset_not_found", 404, "Beatmapset not found")
    SCORE_NOT_FOUND = ("score_not_found", 404, "Score not found")
    SCORE_TOKEN_NOT_FOUND = ("score_token_not_found", 404, "Score token not found")
    ROOM_NOT_FOUND = ("room_not_found", 404, "Room not found")
    PLAYLIST_NOT_FOUND = ("playlist_not_found", 404, "Playlist not found")
    PLAYLIST_ITEM_NOT_FOUND = ("playlist_item_not_found", 404, "Playlist item not found")
    REPLAY_FILE_NOT_FOUND = ("replay_file_not_found", 404, "Replay file not found")
    CHANNEL_NOT_FOUND = ("channel_not_found", 404, "Channel not found")
    TAG_NOT_FOUND = ("tag_not_found", 400, "Tag not found")
    RELATIONSHIP_NOT_FOUND = ("relationship_not_found", 404, "Relationship not found")
    TEAM_NOT_FOUND = ("team_not_found", 404, "Team not found")
    LEADER_NOT_FOUND = ("leader_not_found", 404, "Leader not found")
    LEADER_NOT_TEAM_MEMBER = ("leader_not_team_member", 404, "Leader is not a member of the team")
    USER_NOT_TEAM_MEMBER = ("user_not_team_member", 404, "User is not a member of the team")
    JOIN_REQUEST_NOT_FOUND = ("join_request_not_found", 404, "Join request not found")
    OAUTH_APP_NOT_FOUND = ("oauth_app_not_found", 404, "OAuth app not found")
    AUDIO_PREVIEW_NOT_FOUND = ("audio_preview_not_found", 404, "Audio preview not found for this beatmapset")
    AUDIO_PREVIEW_NOT_AVAILABLE = (
        "audio_preview_not_available",
        404,
        "Audio preview not available for this beatmapset",
    )
    SESSION_NOT_FOUND = ("session_not_found", 404, "Session not found")
    TRUSTED_DEVICE_NOT_FOUND = ("trusted_device_not_found", 404, "Trusted device not found")

    # Validation / bad request
    INVALID_REQUEST = ("invalid_request", 400, "Invalid request")
    INVALID_RULESET_ID = ("invalid_ruleset_id", 422, "Invalid ruleset ID")
    INVALID_BEATMAPSET_TYPE = ("invalid_beatmapset_type", 400, "Invalid beatmapset type")
    BEATMAP_LOOKUP_ARGS_MISSING = (
        "beatmap_lookup_args_missing",
        400,
        "At least one of 'id', 'checksum', or 'filename' must be provided.",
    )
    BEATMAPSET_IDS_TOO_MANY = ("beatmapset_ids_too_many", 413, "beatmapset_ids cannot exceed 50 items")
    INVALID_CLIENT_HASH = ("invalid_client_hash", 422, "Invalid client hash")
    INVALID_OR_MISSING_BEATMAP_HASH = ("invalid_or_missing_beatmap_hash", 422, "Invalid or missing beatmap_hash")
    RULESET_VERSION_CHECK_FAILED = ("ruleset_version_check_failed", 422, "Ruleset version check failed")
    CANNOT_CALCULATE_DIFFICULTY = (
        "cannot_calculate_difficulty",
        422,
        "Cannot calculate difficulty for the specified ruleset",
    )
    ROOM_HAS_ENDED = ("room_has_ended", 400, "Room has ended")
    ROOM_ENDED_CANNOT_ACCEPT_NEW = (
        "room_ended_cannot_accept_new",
        410,
        "Room has ended and cannot accept new participants",
    )
    RULESET_MISMATCH_PLAYLIST_ITEM = ("ruleset_mismatch_playlist_item", 400, "Ruleset mismatch in playlist item")
    BEATMAP_ID_MISMATCH_PLAYLIST_ITEM = (
        "beatmap_id_mismatch_playlist_item",
        400,
        "Beatmap ID mismatch in playlist item",
    )
    PLAYLIST_ITEM_EXPIRED = ("playlist_item_expired", 400, "Playlist item has expired")
    PLAYLIST_ITEM_ALREADY_PLAYED = ("playlist_item_already_played", 400, "Playlist item has already been played")
    SCORE_NOT_PINNED = ("score_not_pinned", 400, "Score is not pinned")
    MAX_ATTEMPTS_REACHED = ("max_attempts_reached", 422, "You have reached the maximum attempts for this room")

    # File / IO
    FILE_SIZE_EXCEEDS_LIMIT = ("file_size_exceeds_limit", 400, "File size exceeds 10MB limit")
    FILE_EMPTY = ("file_empty", 400, "File cannot be empty")
    INVALID_IMAGE_FORMAT = ("invalid_image_format", 400, "Invalid image format")
    IMAGE_DIMENSIONS_EXCEED_LIMIT = ("image_dimensions_exceed_limit", 400, "Image size exceeds the limit")
    ERROR_PROCESSING_IMAGE = ("error_processing_image", 400, "Error processing image")
    AUDIO_FILE_TOO_LARGE = ("audio_file_too_large", 413, "Audio file too large")

    BEATMAPSET_RATING_FORBIDDEN = ("beatmapset_rating_forbidden", 403, "User Cannot Rate This Beatmapset")
    PLAYLIST_EMPTY_ON_CREATION = (
        "playlist_empty_on_creation",
        400,
        "At least one playlist item is required to create a room",
    )

    # Profile / user settings
    INVALID_PROFILE_ORDER = ("invalid_profile_order", 400, "Invalid profile order")
    INVALID_PROFILE_COLOUR_HEX = ("invalid_profile_colour_hex", 400, "Invalid profile colour hex value")
    USERNAME_EXISTS = ("username_exists", 409, "Username Exists")
    NAME_ALREADY_EXISTS = ("name_already_exists", 409, "Name already exists")
    SHORT_NAME_ALREADY_EXISTS = ("short_name_already_exists", 409, "Short name already exists")
    JOIN_REQUEST_ALREADY_EXISTS = ("join_request_already_exists", 409, "Join request already exists")
    USER_ALREADY_TEAM_MEMBER = ("user_already_team_member", 409, "User is already a member of the team")
    NOT_TEAM_LEADER = ("not_team_leader", 403, "You are not the team leader")
    ALREADY_IN_TEAM = ("already_in_team", 403, "You are already in a team")
    CANNOT_LEAVE_AS_TEAM_LEADER = (
        "cannot_leave_as_team_leader",
        403,
        "You cannot leave because you are the team leader",
    )
    CANNOT_DELETE_CURRENT_SESSION = ("cannot_delete_current_session", 400, "Cannot delete the current session")
    CANNOT_DELETE_CURRENT_TRUSTED_DEVICE = (
        "cannot_delete_current_trusted_device",
        400,
        "Cannot delete the current trusted device",
    )

    # Relationship
    CANNOT_CHECK_RELATIONSHIP_WITH_SELF = (
        "cannot_check_relationship_with_self",
        422,
        "Cannot check relationship with yourself",
    )
    CANNOT_ADD_RELATIONSHIP_TO_SELF = ("cannot_add_relationship_to_self", 422, "Cannot add relationship to yourself")
    RELATIONSHIP_TYPE_MISMATCH = ("relationship_type_mismatch", 422, "Relationship type mismatch")

    # TOTP
    TOTP_ALREADY_ENABLED = ("totp_already_enabled", 400, "TOTP is already enabled for this user")
    NO_TOTP_SETUP_OR_INVALID_DATA = ("no_totp_setup_or_invalid_data", 400, "No TOTP setup in progress or invalid data")
    TOO_MANY_FAILED_ATTEMPTS = ("too_many_failed_attempts", 400, "Too many failed attempts. Please start over.")
    INVALID_TOTP_CODE = ("invalid_totp_code", 400, "Invalid TOTP code")
    INVALID_TOTP_FORMAT = (
        "invalid_totp_format",
        400,
        "Invalid TOTP code format. Expected 6-digit code or 10-character backup code.",
    )
    TOTP_NOT_ENABLED = ("totp_not_enabled", 400, "TOTP is not enabled for this user")
    INVALID_TOTP_OR_BACKUP_CODE = ("invalid_totp_or_backup_code", 400, "Invalid TOTP code or backup code")

    # Password & OAuth
    INCORRECT_SIGNIN = ("incorrect_signin", 400, "Username or password incorrect")
    INVALID_SCOPE = ("invalid_scope", 400, "The requested scope is invalid, unknown, "
                                           "or malformed. The client may not request "
                                           "more than one scope at a time.")
    SIGNIN_INFO_REQUIRED = ("signin_info_required", 400, "Username and password required")
    CLIENT_OAUTH_FAILED = ("client_oauth_failed", 401, "Client authentication failed (e.g., unknown client, "
                                                       "no client authentication included, "
                                                       "or unsupported authentication method).")
    INVALID_VERIFICATION_TOKEN = ("invalid_verification_token", 400, "Invalid or expired verification token")
    PASSWORD_INCORRECT = ("password_incorrect", 403, "Current password is incorrect")
    PASSWORD_REQUIRED = ("password_required", 403, "Password required")
    INVALID_PASSWORD = ("invalid_password", 403, "Invalid password")
    ROOM_PASSWORD_REQUIRED = ("room_password_required", 403, "Password required")
    ROOM_INVALID_PASSWORD = ("room_invalid_password", 403, "Invalid password")
    FORBIDDEN_NOT_OWNER = ("forbidden_not_owner", 403, "Forbidden: Not the owner of this app")
    REDIRECT_URI_NOT_ALLOWED = ("redirect_uri_not_allowed", 403, "Redirect URI not allowed for this client")

    # Beatmap / proxy services
    NO_DOWNLOAD_ENDPOINTS_AVAILABLE = ("no_download_endpoints_available", 503, "No download endpoints available")
    FAILED_CONNECT_OSU_SERVERS = ("failed_connect_osu_servers", 503, "Failed to connect to osu! servers")
    INTERNAL_ERROR_FETCHING_AUDIO = ("internal_error_fetching_audio", 500, "Internal server error while fetching audio")


@dataclass
class RequestError(HTTPException):
    """
    A wrapper for major API errors to simplify response composition.

    Attributes:
        msg_key (str): The key of the error message for localization.
        status_code (int): The status code should be responded, defaults to 422 to match osu!api's behavior.
        fallback_msg (str): The fallback message for clients without localization support.

    Args:
        error_type (ErrorType): The error type to initialize from.
        extra (dict[str, Any] | None): Details to include in the response.
        status_code (int): Overrides the default one given by the error type.
        headers (dict[str, str] | None): Will be attached to the response header.
    """

    msg_key: str
    status_code: int = 422
    fallback_msg: str | None = None

    def __init__(
        self,
        error_type: ErrorType,
        extra: dict[str, Any] | None = None,
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.msg_key = error_type.value[0]
        self.status_code = status_code if status_code is not None else error_type.value[1]
        self.fallback_msg = error_type.value[2]

        # Optional details
        detail = {"key": self.msg_key}
        if extra:
            detail.update(extra)

        # Fallback message
        if self.fallback_msg:
            detail.update({"fallback": self.fallback_msg})

        super().__init__(self.status_code, detail=detail, headers=headers)
