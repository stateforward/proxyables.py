
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union, Dict

class ProxyValueKinds(IntEnum):
    FUNCTION = 0x9ed64249
    ARRAY = 0x8a58ad26
    STRING = 0x17c16538
    NUMBER = 0x1bd670a0
    BOOLEAN = 0x65f46ebf
    SYMBOL = 0xf3fb51d1
    OBJECT = 0xb8c60cba
    BIGINT = 0x8a67a5ca
    UNKNOWN = 0x9b759fb9
    NULL = 0x77074ba4
    UNDEFINED = 0x9b61ad43
    REFERENCE = 0x5a1b3c4d

class ProxyInstructionKinds(IntEnum):
    LOCAL = 0x9c436708
    GET = 0x540ca757
    SET = 0xc6270703
    APPLY = 0x24bc4a3b
    CONSTRUCT = 0x40c09172
    EXECUTE = 0xa01e3d98
    THROW = 0x7a78762f
    RETURN = 0x85ee37bf
    NEXT = 0x5cb68de8
    RELEASE = 0x1a2b3c4d

@dataclass
class ProxyInstruction:
    kind: int
    data: Any
    id: Optional[str] = None
    metadata: Optional[Any] = None

@dataclass
class ProxyError:
    message: str
    cause: Optional["ProxyError"] = None
    extra: Dict[str, Any] = field(default_factory=dict)
