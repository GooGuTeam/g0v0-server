from typing import Annotated

from app.service.client_verification_service import (
    ClientVerificationService as OriginalClientVerificationService,
    get_client_verification_service,
)

from fast_depends import Depends as DIDepends
from fastapi import Depends

ClientVerificationService = Annotated[
    OriginalClientVerificationService,
    Depends(get_client_verification_service),
    DIDepends(get_client_verification_service),
]
