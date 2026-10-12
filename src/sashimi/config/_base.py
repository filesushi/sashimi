from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Generic, Self, TypeVar, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from sashimi.errors import InvalidOptionsError

_SENTENCE = "sashimi_sentence"
_UPPER_BOUND_ERRORS = frozenset({"less_than", "less_than_equal"})
_TRUE = frozenset({"true", "1", "on", "yes"})
_FALSE = frozenset({"false", "0", "off", "no"})
TARGET_ALIASES: Mapping[str, str] = MappingProxyType({"jpg": "jpeg", "tif": "tiff"})


def sentence(text: str) -> PydanticCustomError:
    """A validation error whose message is shown to users as it is, as a complete sentence."""
    return PydanticCustomError(_SENTENCE, text)


class ParamsModel(BaseModel):
    """
    Base for every config model.

    - Unknown keys are rejected.
    - A key that is omitted or set to ``None`` means "use the default".
    - Models are immutable, so a config can be shared between calls.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    bound_messages: ClassVar[Mapping[str, str]] = MappingProxyType({})
    """User-facing sentences for fields whose upper bounds are product limits, keyed by field name."""

    @model_validator(mode="before")
    @classmethod
    def _drop_none(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            return {key: value for key, value in data.items() if value is not None}
        return data

    @classmethod
    def from_params(cls, params: Mapping[str, Any] | None = None, /, **fields: Any) -> Self:
        """
        Builds a config from a parameter dict such as FileSushi's job parameters, plus explicit
        fields (e.g. ``target=...``). Invalid values raise :class:`InvalidOptionsError`, whose
        ``user_message`` is one readable sentence.
        """
        data = {**(params or {}), **fields}
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            raise invalid_options_error(e, cls.bound_messages) from e


def invalid_options_error(err: ValidationError, bound_messages: Mapping[str, str] | None = None) -> InvalidOptionsError:
    """Turns the first validation error into an :class:`InvalidOptionsError` with a readable sentence."""
    bound_messages = bound_messages or {}
    first = err.errors()[0]
    field = ".".join(str(part) for part in first["loc"]) or "options"
    if first["type"] == _SENTENCE:
        message = first["msg"]
    elif first["type"] == "extra_forbidden":
        message = f"Unknown option: {field}."
    elif first["type"] in _UPPER_BOUND_ERRORS and field in bound_messages:
        message = bound_messages[field]
    else:
        reason = first["msg"].removeprefix("Value error, ")
        if not reason[1:2].isupper():
            reason = reason[:1].lower() + reason[1:]
        message = f"Invalid value for {field}: {reason}."
        message = message.replace("..", ".")
    return InvalidOptionsError(message, detail=str(err))


def parse_bool(value: Any) -> Any:
    """Accepts the strings a web form posts for a checkbox ("true", "on", "1", ...)."""
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return value


def _parse_int(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return value


def normalize_target(value: Any) -> Any:
    """Lower-cases a format name and maps ``jpg``/``tif`` to ``jpeg``/``tiff``."""
    if isinstance(value, str):
        value = value.strip().lower()
        return TARGET_ALIASES.get(value, value)
    return value


FormInt = BeforeValidator(_parse_int)
"""Lets an integer ``Literal`` field accept the string a web form posts ("24" for 24)."""
FormBool = BeforeValidator(parse_bool)
"""Lets a boolean ``Literal`` field accept the string a web form posts ("true" for True)."""
TargetName = BeforeValidator(normalize_target)


def parse_color(value: Any) -> Any:
    """Accepts '#rgb', '#rrggbb', '#rrggbbaa', 'r,g,b[,a]' or a sequence of ints."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("#"):
            digits = text[1:]
            if len(digits) == 3:
                digits = "".join(c * 2 for c in digits)
            if len(digits) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in digits):
                raise ValueError(f"not a colour: {value!r}")
            return tuple(int(digits[i : i + 2], 16) for i in range(0, len(digits), 2))
        try:
            return tuple(int(part) for part in text.replace(" ", "").split(","))
        except ValueError as e:
            raise ValueError(f"not a colour: {value!r}") from e
    if isinstance(value, list):
        return tuple(value)
    return value


_Channel = Annotated[int, Field(ge=0, le=255)]
RGB = Annotated[tuple[_Channel, _Channel, _Channel], BeforeValidator(parse_color)]
"""An RGB colour: a 3-tuple, ``"#rrggbb"`` or ``"r,g,b"``."""
RGBA = Annotated[tuple[_Channel, _Channel, _Channel, _Channel], BeforeValidator(parse_color)]
"""An RGBA colour: a 4-tuple, ``"#rrggbbaa"`` or ``"r,g,b,a"``."""


C = TypeVar("C")


def _choices(values: list[str]) -> str:
    return values[0] if len(values) == 1 else f"{', '.join(values[:-1])} or {values[-1]}"


class ConfigFamily(Generic[C]):
    """
    The config models of one operation, one per output format.

    :meth:`from_params` picks the model from the discriminating option (``target`` unless stated)
    and validates the remaining options against it; :meth:`model_for` returns that model without
    validating anything, for reading its fields, defaults and bounds.
    """

    def __init__(
        self, name: str, models: Mapping[str, type[ParamsModel]], *, key: str = "target", default: str | None = None
    ):
        self.name = name
        self.key = key
        self.default = default
        self._models = MappingProxyType(dict(models))

    @property
    def models(self) -> tuple[type[C], ...]:
        """Every model in the family."""
        return tuple(cast(type[C], model) for model in dict.fromkeys(self._models.values()))

    @property
    def targets(self) -> tuple[str, ...]:
        """The values the discriminating option accepts."""
        return tuple(self._models)

    def __repr__(self) -> str:
        return f"<ConfigFamily {self.name}: {', '.join(m.__name__ for m in self.models)}>"

    def model_for(self, **discriminators: Any) -> type[C]:
        """The model used for these discriminating options, e.g. ``model_for(target="jpeg")``."""
        model, _ = self._pick(discriminators)
        return cast(type[C], model)

    def from_params(self, params: Mapping[str, Any] | None = None, /, **fields: Any) -> C:
        """
        Picks the model and builds the config from `params` plus explicit `fields` (which win).
        Anything invalid, including an unknown or missing format, raises
        :class:`~sashimi.errors.InvalidOptionsError`.
        """
        data = {key: value for key, value in {**(params or {}), **fields}.items() if value is not None}
        model, data = self._pick(data)
        return cast(C, model.from_params(data))

    def _lookup(
        self, key: str, value: Any, table: Mapping[str, type[ParamsModel]], default: str | None
    ) -> tuple[str, type[ParamsModel]]:
        if value is None:
            if default is None:
                raise InvalidOptionsError(f"Missing option: {key}.")
            value = default
        name = normalize_target(value)
        if not isinstance(name, str) or name not in table:
            raise InvalidOptionsError(f"Invalid value for {key}: must be {_choices(list(table))}.")
        return name, table[name]

    def _pick(self, data: Mapping[str, Any]) -> tuple[type[ParamsModel], dict[str, Any]]:
        name, model = self._lookup(self.key, data.get(self.key), self._models, self.default)
        return model, {**data, self.key: name}


def parse_flag(key: str, value: Any) -> bool:
    """Reads a boolean discriminator such as ``accurate`` or ``animated``."""
    parsed = parse_bool(value)
    if not isinstance(parsed, bool):
        raise InvalidOptionsError(f"Invalid value for {key}: input should be a valid boolean.")
    return parsed
