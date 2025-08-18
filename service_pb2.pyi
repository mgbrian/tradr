from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PlaceStockOrderRequest(_message.Message):
    __slots__ = ("symbol", "side", "quantity", "order_type", "price", "tif")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    side: str
    quantity: int
    order_type: str
    price: float
    tif: str
    def __init__(self, symbol: _Optional[str] = ..., side: _Optional[str] = ..., quantity: _Optional[int] = ..., order_type: _Optional[str] = ..., price: _Optional[float] = ..., tif: _Optional[str] = ...) -> None: ...

class PlaceOptionOrderRequest(_message.Message):
    __slots__ = ("symbol", "expiry", "strike", "right", "side", "quantity", "order_type", "price", "tif")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    EXPIRY_FIELD_NUMBER: _ClassVar[int]
    STRIKE_FIELD_NUMBER: _ClassVar[int]
    RIGHT_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    expiry: str
    strike: float
    right: str
    side: str
    quantity: int
    order_type: str
    price: float
    tif: str
    def __init__(self, symbol: _Optional[str] = ..., expiry: _Optional[str] = ..., strike: _Optional[float] = ..., right: _Optional[str] = ..., side: _Optional[str] = ..., quantity: _Optional[int] = ..., order_type: _Optional[str] = ..., price: _Optional[float] = ..., tif: _Optional[str] = ...) -> None: ...

class PlaceOrderResponse(_message.Message):
    __slots__ = ("order_id", "broker_order_id", "status", "message")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    order_id: int
    broker_order_id: int
    status: str
    message: str
    def __init__(self, order_id: _Optional[int] = ..., broker_order_id: _Optional[int] = ..., status: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class GetOrderRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: int
    def __init__(self, order_id: _Optional[int] = ...) -> None: ...

class OrderRecord(_message.Message):
    __slots__ = ("order_id", "broker_order_id", "asset_class", "symbol", "side", "quantity", "status", "avg_price", "filled_qty", "message")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ASSET_CLASS_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    AVG_PRICE_FIELD_NUMBER: _ClassVar[int]
    FILLED_QTY_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    order_id: int
    broker_order_id: int
    asset_class: str
    symbol: str
    side: str
    quantity: int
    status: str
    avg_price: float
    filled_qty: int
    message: str
    def __init__(self, order_id: _Optional[int] = ..., broker_order_id: _Optional[int] = ..., asset_class: _Optional[str] = ..., symbol: _Optional[str] = ..., side: _Optional[str] = ..., quantity: _Optional[int] = ..., status: _Optional[str] = ..., avg_price: _Optional[float] = ..., filled_qty: _Optional[int] = ..., message: _Optional[str] = ...) -> None: ...

class ListOrdersRequest(_message.Message):
    __slots__ = ("limit",)
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    limit: int
    def __init__(self, limit: _Optional[int] = ...) -> None: ...

class ListOrdersResponse(_message.Message):
    __slots__ = ("orders",)
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    orders: _containers.RepeatedCompositeFieldContainer[OrderRecord]
    def __init__(self, orders: _Optional[_Iterable[_Union[OrderRecord, _Mapping]]] = ...) -> None: ...

class ListFillsRequest(_message.Message):
    __slots__ = ("order_id", "limit")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    order_id: int
    limit: int
    def __init__(self, order_id: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class FillRecord(_message.Message):
    __slots__ = ("fill_id", "order_id", "exec_id", "price", "filled_qty", "symbol", "side", "time", "broker_order_id")
    FILL_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    FILLED_QTY_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    TIME_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    fill_id: int
    order_id: int
    exec_id: str
    price: float
    filled_qty: int
    symbol: str
    side: str
    time: str
    broker_order_id: int
    def __init__(self, fill_id: _Optional[int] = ..., order_id: _Optional[int] = ..., exec_id: _Optional[str] = ..., price: _Optional[float] = ..., filled_qty: _Optional[int] = ..., symbol: _Optional[str] = ..., side: _Optional[str] = ..., time: _Optional[str] = ..., broker_order_id: _Optional[int] = ...) -> None: ...

class ListFillsResponse(_message.Message):
    __slots__ = ("fills",)
    FILLS_FIELD_NUMBER: _ClassVar[int]
    fills: _containers.RepeatedCompositeFieldContainer[FillRecord]
    def __init__(self, fills: _Optional[_Iterable[_Union[FillRecord, _Mapping]]] = ...) -> None: ...

class GetPositionsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class PositionRecord(_message.Message):
    __slots__ = ("account", "symbol", "sec_type", "exchange", "con_id", "position", "avg_cost")
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    SEC_TYPE_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    CON_ID_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    AVG_COST_FIELD_NUMBER: _ClassVar[int]
    account: str
    symbol: str
    sec_type: str
    exchange: str
    con_id: int
    position: float
    avg_cost: float
    def __init__(self, account: _Optional[str] = ..., symbol: _Optional[str] = ..., sec_type: _Optional[str] = ..., exchange: _Optional[str] = ..., con_id: _Optional[int] = ..., position: _Optional[float] = ..., avg_cost: _Optional[float] = ...) -> None: ...

class GetPositionsResponse(_message.Message):
    __slots__ = ("positions",)
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    positions: _containers.RepeatedCompositeFieldContainer[PositionRecord]
    def __init__(self, positions: _Optional[_Iterable[_Union[PositionRecord, _Mapping]]] = ...) -> None: ...

class GetAccountValuesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class AccountValueRecord(_message.Message):
    __slots__ = ("account", "tag", "currency", "value")
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    TAG_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    account: str
    tag: str
    currency: str
    value: str
    def __init__(self, account: _Optional[str] = ..., tag: _Optional[str] = ..., currency: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class GetAccountValuesResponse(_message.Message):
    __slots__ = ("account_values",)
    ACCOUNT_VALUES_FIELD_NUMBER: _ClassVar[int]
    account_values: _containers.RepeatedCompositeFieldContainer[AccountValueRecord]
    def __init__(self, account_values: _Optional[_Iterable[_Union[AccountValueRecord, _Mapping]]] = ...) -> None: ...
