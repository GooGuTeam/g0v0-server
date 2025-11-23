from collections.abc import Awaitable, Callable
from functools import lru_cache, wraps
import inspect
import sys
from types import NoneType, get_original_bases
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Concatenate,
    ForwardRef,
    ParamSpec,
    TypedDict,
    cast,
    get_args,
    get_origin,
    overload,
)

from app.models.model import UTCBaseModel
from app.utils import type_is_optional

from sqlalchemy.ext.asyncio import async_object_session
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.main import SQLModelMetaclass

_dict_to_model: dict[type, type["DatabaseModel"]] = {}


def _safe_evaluate_forwardref(type_: str | ForwardRef, module_name: str) -> Any:
    """Safely evaluate a ForwardRef, with fallback to app.database module"""
    if isinstance(type_, str):
        type_ = ForwardRef(type_)

    try:
        return evaluate_forwardref(
            type_,
            globalns=vars(sys.modules[module_name]),
            localns={},
        )
    except (NameError, AttributeError, KeyError):
        # Fallback to app.database module
        try:
            import app.database

            return evaluate_forwardref(
                type_,
                globalns=vars(app.database),
                localns={},
            )
        except (NameError, AttributeError, KeyError):
            return None


class OnDemand[T]:
    if TYPE_CHECKING:

        def __get__(self, instance: object | None, owner: Any) -> T: ...

        def __set__(self, instance: Any, value: T) -> None: ...

        def __delete__(self, instance: Any) -> None: ...


class Exclude[T]:
    if TYPE_CHECKING:

        def __get__(self, instance: object | None, owner: Any) -> T: ...

        def __set__(self, instance: Any, value: T) -> None: ...

        def __delete__(self, instance: Any) -> None: ...


# https://github.com/fastapi/sqlmodel/blob/main/sqlmodel/_compat.py#L126-L140
def _get_annotations(class_dict: dict[str, Any]) -> dict[str, Any]:
    raw_annotations: dict[str, Any] = class_dict.get("__annotations__", {})
    if sys.version_info >= (3, 14) and "__annotations__" not in class_dict:
        # See https://github.com/pydantic/pydantic/pull/11991
        from annotationlib import (
            Format,
            call_annotate_function,
            get_annotate_from_class_namespace,
        )

        if annotate := get_annotate_from_class_namespace(class_dict):
            raw_annotations = call_annotate_function(annotate, format=Format.FORWARDREF)
    return raw_annotations


# https://github.com/pydantic/pydantic/blob/main/pydantic/v1/typing.py#L58-L77
if sys.version_info < (3, 12, 4):

    def evaluate_forwardref(type_: ForwardRef, globalns: Any, localns: Any) -> Any:
        # Even though it is the right signature for python 3.9, mypy complains with
        # `error: Too many arguments for "_evaluate" of "ForwardRef"` hence the cast...
        # Python 3.13/3.12.4+ made `recursive_guard` a kwarg, so name it explicitly to avoid:
        # TypeError: ForwardRef._evaluate() missing 1 required keyword-only argument: 'recursive_guard'
        return cast(Any, type_)._evaluate(globalns, localns, recursive_guard=set())

else:

    def evaluate_forwardref(type_: ForwardRef, globalns: Any, localns: Any) -> Any:
        # Pydantic 1.x will not support PEP 695 syntax, but provide `type_params` to avoid
        # warnings:
        return cast(Any, type_)._evaluate(globalns, localns, type_params=(), recursive_guard=set())


class DatabaseModelMetaclass(SQLModelMetaclass):
    def __new__(
        cls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> "DatabaseModelMetaclass":
        original_annotations = _get_annotations(namespace)
        new_annotations = {}
        ondemands = []
        excludes = []

        for k, v in original_annotations.items():
            if get_origin(v) is OnDemand:
                inner_type = v.__args__[0]
                new_annotations[k] = inner_type
                ondemands.append(k)
            elif get_origin(v) is Exclude:
                inner_type = v.__args__[0]
                new_annotations[k] = inner_type
                excludes.append(k)
            else:
                new_annotations[k] = v

        new_class = super().__new__(
            cls,
            name,
            bases,
            {
                **namespace,
                "__annotations__": new_annotations,
            },
            **kwargs,
        )

        new_class._CALCULATED_FIELDS = dict(getattr(new_class, "_CALCULATED_FIELDS", {}))
        new_class._ONDEMAND_DATABASE_FIELDS = list(getattr(new_class, "_ONDEMAND_DATABASE_FIELDS", [])) + list(
            ondemands
        )
        new_class._ONDEMAND_CALCULATED_FIELDS = dict(getattr(new_class, "_ONDEMAND_CALCULATED_FIELDS", {}))
        new_class._EXCLUDED_DATABASE_FIELDS = list(getattr(new_class, "_EXCLUDED_DATABASE_FIELDS", [])) + list(excludes)

        for attr_name, attr_value in namespace.items():
            target = _get_callable_target(attr_value)
            if target is None:
                continue
            if getattr(target, "__included__", False):
                new_class._CALCULATED_FIELDS[attr_name] = _get_return_type(target)
            if getattr(target, "__calculated_ondemand__", False):
                new_class._ONDEMAND_CALCULATED_FIELDS[attr_name] = _get_return_type(target)

        # Register TDict to DatabaseModel mapping
        for base in get_original_bases(new_class):
            cls_name = base.__name__
            if "DatabaseModel" in cls_name and "[" in cls_name and "]" in cls_name:
                generic_type_name = cls_name[cls_name.index("[") : cls_name.rindex("]") + 1]
                generic_type = evaluate_forwardref(
                    ForwardRef(generic_type_name),
                    globalns=vars(sys.modules[new_class.__module__]),
                    localns={},
                )
                _dict_to_model[generic_type[0]] = new_class

        return new_class


def _get_callable_target(value: Any) -> Callable | None:
    if isinstance(value, (staticmethod, classmethod)):
        return value.__func__
    if inspect.isfunction(value):
        return value
    if inspect.ismethod(value):
        return value.__func__
    return None


def _mark_callable(value: Any, flag: str) -> Callable | None:
    target = _get_callable_target(value)
    if target is None:
        return None
    setattr(target, flag, True)
    return target


def _get_return_type(func: Callable) -> type:
    sig = inspect.get_annotations(func)
    return sig.get("return", Any)


P = ParamSpec("P")
CalculatedField = Callable[Concatenate[AsyncSession, Any, P], Awaitable[Any]]
DecoratorTarget = CalculatedField | staticmethod | classmethod


def included(func: DecoratorTarget) -> DecoratorTarget:
    marker = _mark_callable(func, "__included__")
    if marker is None:
        raise RuntimeError("@included is only usable on callables.")

    @wraps(marker)
    async def wrapper(*args, **kwargs):
        return await marker(*args, **kwargs)

    if isinstance(func, staticmethod):
        return staticmethod(wrapper)
    if isinstance(func, classmethod):
        return classmethod(wrapper)
    return wrapper


def ondemand(func: DecoratorTarget) -> DecoratorTarget:
    marker = _mark_callable(func, "__calculated_ondemand__")
    if marker is None:
        raise RuntimeError("@ondemand is only usable on callables.")

    @wraps(marker)
    async def wrapper(*args, **kwargs):
        return await marker(*args, **kwargs)

    if isinstance(func, staticmethod):
        return staticmethod(wrapper)
    if isinstance(func, classmethod):
        return classmethod(wrapper)
    return wrapper


async def call_awaitable_with_context(
    func: CalculatedField,
    session: AsyncSession,
    instance: Any,
    context: dict[str, Any],
) -> Any:
    sig = inspect.signature(func)

    if len(sig.parameters) == 2:
        return await func(session, instance)
    else:
        call_params = {}
        for param in sig.parameters.values():
            if param.name in context:
                call_params[param.name] = context[param.name]
        return await func(session, instance, **call_params)


class DatabaseModel[TDict](SQLModel, UTCBaseModel, metaclass=DatabaseModelMetaclass):
    _CALCULATED_FIELDS: ClassVar[dict[str, type]] = {}

    _ONDEMAND_DATABASE_FIELDS: ClassVar[list[str]] = []
    _ONDEMAND_CALCULATED_FIELDS: ClassVar[dict[str, type]] = {}

    _EXCLUDED_DATABASE_FIELDS: ClassVar[list[str]] = []

    @overload
    @classmethod
    async def transform(
        cls,
        db_instance: "DatabaseModel",
        *,
        session: AsyncSession,
        includes: list[str] | None = None,
        **context: Any,
    ) -> TDict: ...

    @overload
    @classmethod
    async def transform(
        cls,
        db_instance: "DatabaseModel",
        *,
        includes: list[str] | None = None,
        **context: Any,
    ) -> TDict: ...

    @classmethod
    async def transform(
        cls,
        db_instance: "DatabaseModel",
        *,
        session: AsyncSession | None = None,
        includes: list[str] | None = None,
        **context: Any,
    ) -> TDict:
        includes = includes.copy() if includes is not None else []
        session = cast(AsyncSession | None, async_object_session(db_instance)) if session is None else session
        if session is None:
            raise RuntimeError("DatabaseModel.transform requires a session-bound instance.")
        resp_obj = cls.model_validate(db_instance.model_dump())
        data = resp_obj.model_dump()

        for field in cls._CALCULATED_FIELDS:
            func = getattr(cls, field)
            value = await call_awaitable_with_context(func, session, db_instance, context)
            data[field] = value

        sub_include_map: dict[str, list[str]] = {}
        for include in [i for i in includes if "." in i]:
            parent, sub_include = include.split(".", 1)
            if parent not in sub_include_map:
                sub_include_map[parent] = []
            sub_include_map[parent].append(sub_include)
            includes.remove(include)  # pyright: ignore[reportOptionalMemberAccess]

        for field, sub_includes in sub_include_map.items():
            if field in cls._ONDEMAND_CALCULATED_FIELDS:
                func = getattr(cls, field)
                value = await call_awaitable_with_context(
                    func, session, db_instance, {**context, "includes": sub_includes}
                )
                data[field] = value

        for include in includes:
            if include in data:
                continue

            if include in cls._ONDEMAND_CALCULATED_FIELDS:
                func = getattr(cls, include)
                value = await call_awaitable_with_context(func, session, db_instance, context)
                data[include] = value

        for field in cls._ONDEMAND_DATABASE_FIELDS:
            if field not in includes:
                del data[field]

        for field in cls._EXCLUDED_DATABASE_FIELDS:
            if field in data:
                del data[field]

        return cast(TDict, data)

    @classmethod
    @lru_cache
    def generate_typeddict(cls, includes: tuple[str, ...] | None = None) -> type[TypedDict]:  # pyright: ignore[reportInvalidTypeForm]
        def _evaluate_type(field_type: Any, *, resolve_database_model: bool = False, field_name: str = "") -> Any:
            # Evaluate ForwardRef if present
            if isinstance(field_type, (str, ForwardRef)):
                resolved = _safe_evaluate_forwardref(field_type, cls.__module__)
                if resolved is not None:
                    field_type = resolved

            origin_type = get_origin(field_type)
            inner_type = field_type
            args = get_args(field_type)

            is_optional = type_is_optional(field_type)  # pyright: ignore[reportArgumentType]
            if is_optional:
                inner_type = next((arg for arg in args if arg is not NoneType), field_type)

            is_list = False
            if origin_type is list:
                is_list = True
                inner_type = args[0]

            # Evaluate ForwardRef in inner_type if present
            if isinstance(inner_type, (str, ForwardRef)):
                resolved = _safe_evaluate_forwardref(inner_type, cls.__module__)
                if resolved is not None:
                    inner_type = resolved

            if not resolve_database_model:
                if is_optional:
                    return inner_type | None  # pyright: ignore[reportOperatorIssue]
                elif is_list:
                    return list[inner_type]
                return inner_type

            model_class = None

            # First check if inner_type is directly a DatabaseModel subclass
            try:
                if inspect.isclass(inner_type) and issubclass(inner_type, DatabaseModel):  # type: ignore
                    model_class = inner_type
            except TypeError:
                pass

            # If not found, look up in _dict_to_model
            if model_class is None:
                model_class = _dict_to_model.get(inner_type)  # type: ignore

            if model_class is not None:
                nested_dict = model_class.generate_typeddict(tuple(sub_include_map.get(field_name, ())))
                resolved_type = list[nested_dict] if is_list else nested_dict  # type: ignore

                if is_optional:
                    resolved_type = resolved_type | None  # type: ignore

                return resolved_type

            # Fallback: use the resolved inner_type
            resolved_type = list[inner_type] if is_list else inner_type  # type: ignore
            if is_optional:
                resolved_type = resolved_type | None  # type: ignore
            return resolved_type

        if includes is None:
            includes = ()

        # Parse nested includes
        direct_includes = []
        sub_include_map: dict[str, list[str]] = {}
        for include in includes:
            if "." in include:
                parent, sub_include = include.split(".", 1)
                if parent not in sub_include_map:
                    sub_include_map[parent] = []
                sub_include_map[parent].append(sub_include)
                if parent not in direct_includes:
                    direct_includes.append(parent)
            else:
                direct_includes.append(include)

        fields = {}

        # Process model fields
        for field_name, field_info in cls.model_fields.items():
            field_type = field_info.annotation or Any
            field_type = _evaluate_type(field_type, field_name=field_name)

            if field_name in cls._ONDEMAND_DATABASE_FIELDS and field_name not in direct_includes:
                continue
            else:
                fields[field_name] = field_type

        # Process calculated fields
        for field_name, field_type in cls._CALCULATED_FIELDS.items():
            field_type = _evaluate_type(field_type, resolve_database_model=True, field_name=field_name)
            fields[field_name] = field_type

        # Process ondemand calculated fields
        for field_name, field_type in cls._ONDEMAND_CALCULATED_FIELDS.items():
            if field_name not in direct_includes:
                continue

            field_type = _evaluate_type(field_type, resolve_database_model=True, field_name=field_name)
            fields[field_name] = field_type

        return TypedDict(f"{cls.__name__}Dict[{', '.join(includes)}]" if includes else f"{cls.__name__}Dict", fields)  # pyright: ignore[reportArgumentType]
