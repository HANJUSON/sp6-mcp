"""JSON setting-sheet model (README section 8) with strict structural checks.

Pydantic only checks shape and ranges. Cross-field rules (overlaps, channel
sharing, template dependencies, codec support) live in ``validate.py``.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import INVALID_SPEC, Sp6Error

Midi = Annotated[int, Field(ge=0, le=127, strict=True)]
Velocity = Annotated[int, Field(ge=1, le=127, strict=True)]
Level = Union[Annotated[int, Field(ge=0, le=127, strict=True)], Literal["none"]]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def _no_null(cls, data):
        # README 5.1: JSON null is never a substitute for "none" or omission.
        if isinstance(data, dict):
            for key, value in data.items():
                if value is None:
                    raise ValueError(f"'{key}' is null; omit the field to keep the template value or use \"none\" where allowed")
        return data


class ControllerSpec(_Strict):
    enabled: bool | None = None
    scale_percent: Annotated[int, Field(ge=-100, le=100, strict=True)] | None = None
    entry: Level | None = None


class ControllersSpec(_Strict):
    modwheel: ControllerSpec | None = None
    filter1: ControllerSpec | None = None


class ZoneSpec(_Strict):
    zone: Annotated[int, Field(ge=1, le=4, strict=True)]
    enabled: bool | None = None
    program_id: Annotated[int, Field(ge=1, le=2048, strict=True)] | None = None
    midi_channel: Annotated[int, Field(ge=1, le=16, strict=True)] | None = None
    key_low: Midi | None = None
    key_high: Midi | None = None
    allow_inverted_key_range: bool = False
    allow_inverted_velocity_range: bool = False
    transpose_semitones: Annotated[int, Field(ge=-128, le=127, strict=True)] | None = None
    velocity_low: Velocity | None = None
    velocity_high: Velocity | None = None
    volume: Level | None = None
    volume_entry: Level | None = None
    pan: Level | None = None
    arp_button: Literal["yes", "no"] | None = None
    controllers: ControllersSpec | None = None

    def provided(self) -> dict:
        """Fields explicitly given (excluding the zone number and policy flags)."""
        data = self.model_dump(exclude_unset=True, exclude_none=True)
        for key in ("zone", "allow_inverted_key_range", "allow_inverted_velocity_range"):
            data.pop(key, None)
        return data


class TemplateRef(_Strict):
    source_path: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    kind: Literal["multi"]
    id: Annotated[int, Field(ge=1, le=2048, strict=True)]


class MultiSpec(_Strict):
    schema_version: Literal["1"]
    operation: Literal["create_multi"]
    name: Annotated[str, Field(min_length=1)]
    preferred_user_multi_id: Annotated[int, Field(ge=1025, le=2048, strict=True)]
    template: TemplateRef
    range_policy: Literal["split", "layer"] = "split"
    zones: Annotated[list[ZoneSpec], Field(min_length=1, max_length=4)]

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if len(value) > 16:
            raise ValueError("Multi names are limited to 16 characters (Musician's Guide 4-11)")
        if any(ord(c) < 0x20 or ord(c) > 0x7E for c in value):
            raise ValueError("Name must be printable ASCII; no lossy conversion is done")
        return value

    @model_validator(mode="after")
    def _unique_zones(self) -> "MultiSpec":
        numbers = [z.zone for z in self.zones]
        if len(set(numbers)) != len(numbers):
            raise ValueError("Each zone number may appear only once")
        return self

    def zone(self, number: int) -> ZoneSpec | None:
        for z in self.zones:
            if z.zone == number:
                return z
        return None


def _loc_to_path(loc: tuple) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += ("." if path else "") + str(part)
    return path


def _find_null(value: object, path: str) -> str | None:
    if value is None:
        return path or "$"
    if isinstance(value, dict):
        for key, item in value.items():
            found = _find_null(item, f"{path}.{key}" if path else str(key))
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_null(item, f"{path}[{index}]")
            if found:
                return found
    return None


def parse_spec(data: object) -> MultiSpec:
    """Parse a JSON object into ``MultiSpec`` or raise INVALID_SPEC with a field path."""
    null_path = _find_null(data, "")
    if null_path is not None:
        raise Sp6Error(
            INVALID_SPEC,
            "null is not allowed; omit the field to keep the template value or use \"none\" where allowed",
            null_path,
        )
    try:
        return MultiSpec.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"field": _loc_to_path(e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors(include_url=False)
        ]
        first = errors[0]
        raise Sp6Error(INVALID_SPEC, first["message"], first["field"], details={"errors": errors}) from None


def spec_json_schema() -> dict:
    return MultiSpec.model_json_schema()
