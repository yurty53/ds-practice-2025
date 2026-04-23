from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ReadRequest(_message.Message):
    __slots__ = ("key",)
    KEY_FIELD_NUMBER: _ClassVar[int]
    key: str
    def __init__(self, key: _Optional[str] = ...) -> None: ...

class ReadResponse(_message.Message):
    __slots__ = ("value", "version", "found")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    FOUND_FIELD_NUMBER: _ClassVar[int]
    value: str
    version: int
    found: bool
    def __init__(self, value: _Optional[str] = ..., version: _Optional[int] = ..., found: bool = ...) -> None: ...

class WriteRequest(_message.Message):
    __slots__ = ("key", "value", "expected_version")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: str
    expected_version: int
    def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ..., expected_version: _Optional[int] = ...) -> None: ...

class WriteResponse(_message.Message):
    __slots__ = ("success", "version", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    version: int
    error: str
    def __init__(self, success: bool = ..., version: _Optional[int] = ..., error: _Optional[str] = ...) -> None: ...
