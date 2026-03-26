
import msgpack
from dataclasses import is_dataclass, asdict
from .types import ProxyInstruction, ProxyError

def encode_hook(obj):
    if is_dataclass(obj):
        d = asdict(obj)
        # Remove None values to save space? TS doesn't strictly require it but nice.
        return {k: v for k, v in d.items() if v is not None}
    if isinstance(obj, ProxyError):
         return {
             "message": obj.message,
             "cause": obj.cause,
             **obj.extra
         }
    return obj

def encode(data):
    return msgpack.packb(data, default=encode_hook, use_bin_type=True)
