from typing import Annotated

from app.service.client_verification_service import (
    ClientVerificationService as OriginalClientVerificationService,
    get_client_verification_service,
)

from fastapi import Depends

ClientVerificationService = Annotated[OriginalClientVerificationService, Depends(get_client_verification_service)]
