
import msgpack
from .types import ProxyInstruction, ProxyError

def decode_hook(obj):
    # Heuristic to detect ProxyInstruction
    if isinstance(obj, dict):
        if "kind" in obj:
            # It's an instruction
            return ProxyInstruction(**obj)
        # Error?
        if "message" in obj and len(obj) == 1: # Weak heuristic
             pass
    return obj

def decode(data):
    return msgpack.unpackb(data, object_hook=decode_hook, raw=False)
