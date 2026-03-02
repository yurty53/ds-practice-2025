from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class SuggestionsRequest(_message.Message):
    __slots__ = ("book", "order_amount")
    BOOK_FIELD_NUMBER: _ClassVar[int]
    ORDER_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    book: str
    order_amount: float
    def __init__(self, book: _Optional[str] = ..., order_amount: _Optional[float] = ...) -> None: ...

class SuggestionsResponse(_message.Message):
    __slots__ = ("is_fraud",)
    IS_FRAUD_FIELD_NUMBER: _ClassVar[int]
    is_fraud: bool
    def __init__(self, is_fraud: bool = ...) -> None: ...
