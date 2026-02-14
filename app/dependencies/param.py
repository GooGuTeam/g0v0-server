from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError


def BodyOrForm[T: BaseModel](model: type[T]):  # noqa: N802
    """
    A dependency that can parse either JSON body or form data into a Pydantic model.
    It checks the Content-Type header to determine how to parse the incoming data.
    If the Content-Type is application/json, it will attempt to parse the body as JSON.
    Otherwise, it will treat the incoming data as form data.

    Args:
        model: The Pydantic model class to parse the data into.

    Returns:
        A dependency function that can be used in FastAPI routes.
    """

    async def dependency(
        request: Request,
    ) -> T:
        content_type = request.headers.get("content-type", "")

        data: dict[str, Any] = {}
        if "application/json" in content_type:
            try:
                data = await request.json()
            except Exception:
                raise RequestValidationError(
                    [
                        {
                            "loc": ("body",),
                            "msg": "Invalid JSON body",
                            "type": "value_error",
                        }
                    ]
                )
        else:
            form = await request.form()
            data = dict(form)

        try:
            return model(**data)
        except ValidationError as e:
            raise RequestValidationError(e.errors())

    return dependency
