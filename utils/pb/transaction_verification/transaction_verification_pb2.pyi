from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class TransactionRequest(_message.Message):
    __slots__ = ("card_number", "items")
    CARD_NUMBER_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    card_number: str
    items: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, card_number: _Optional[str] = ..., items: _Optional[_Iterable[str]] = ...) -> None: ...

class TransactionResponse(_message.Message):
    __slots__ = ("is_valid", "reason")
    IS_VALID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    is_valid: bool
    reason: str
    def __init__(self, is_valid: bool = ..., reason: _Optional[str] = ...) -> None: ...
