from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OrderItem(_message.Message):
    __slots__ = ("name", "quantity")
    NAME_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    name: str
    quantity: int
    def __init__(self, name: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class Order(_message.Message):
    __slots__ = ("order_id", "items", "user_name", "user_contact")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    USER_NAME_FIELD_NUMBER: _ClassVar[int]
    USER_CONTACT_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    items: _containers.RepeatedCompositeFieldContainer[OrderItem]
    user_name: str
    user_contact: str
    def __init__(self, order_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[OrderItem, _Mapping]]] = ..., user_name: _Optional[str] = ..., user_contact: _Optional[str] = ...) -> None: ...

class EnqueueRequest(_message.Message):
    __slots__ = ("order",)
    ORDER_FIELD_NUMBER: _ClassVar[int]
    order: Order
    def __init__(self, order: _Optional[_Union[Order, _Mapping]] = ...) -> None: ...

class EnqueueResponse(_message.Message):
    __slots__ = ("success",)
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    success: bool
    def __init__(self, success: bool = ...) -> None: ...

class DequeueRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class DequeueResponse(_message.Message):
    __slots__ = ("success", "order")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    success: bool
    order: Order
    def __init__(self, success: bool = ..., order: _Optional[_Union[Order, _Mapping]] = ...) -> None: ...
