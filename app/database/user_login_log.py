"""User login log database models.

This module tracks user login attempts including IP addresses,
geolocation data, and login status.
"""

from datetime import datetime

from app.utils import utcnow

from sqlmodel import Field, SQLModel


class UserLoginLog(SQLModel, table=True):
    """Database table for user login logs."""

    __tablename__: str = "user_login_log"

    id: int | None = Field(default=None, primary_key=True, description="Record ID")
    user_id: int = Field(index=True, description="User ID")
    ip_address: str = Field(max_length=45, index=True, description="IP address (supports IPv4 and IPv6)")
    user_agent: str | None = Field(default=None, max_length=500, description="User agent information")
    login_time: datetime = Field(default_factory=utcnow, description="Login time")

    # GeoIP information
    country_code: str | None = Field(default=None, max_length=2, description="Country code")
    country_name: str | None = Field(default=None, max_length=100, description="Country name")
    city_name: str | None = Field(default=None, max_length=100, description="City name")
    latitude: str | None = Field(default=None, max_length=20, description="Latitude")
    longitude: str | None = Field(default=None, max_length=20, description="Longitude")
    time_zone: str | None = Field(default=None, max_length=50, description="Time zone")

    # ASN information
    asn: int | None = Field(default=None, description="Autonomous System Number")
    organization: str | None = Field(default=None, max_length=200, description="Organization name")

    # Login status
    login_success: bool = Field(default=True, description="Whether the login was successful")
    login_method: str = Field(max_length=50, description="Login method (password/oauth/etc.)")

    # Additional information
    notes: str | None = Field(default=None, max_length=500, description="Additional notes")

    class Config:
        from_attributes = True
