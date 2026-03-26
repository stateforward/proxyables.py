
import struct
from dataclasses import dataclass
from .consts import HEADER_LENGTH

@dataclass
class Frame:
    version: int
    type: int
    flags: int
    stream_id: int
    length: int
    payload: bytes = b""

    def encode(self) -> bytes:
        # struct format: !BBHI I
        # ! = network (big-endian)
        # B = version (1 byte)
        # B = type (1 byte)
        # H = flags (2 bytes)
        # I = stream_id (4 bytes)
        # I = length (4 bytes)
        header = struct.pack(
            "!BBHII", 
            self.version, 
            self.type, 
            self.flags, 
            self.stream_id, 
            self.length
        )
        return header + self.payload

    @classmethod
    def decode_header(cls, data: bytes) -> "Frame":
        if len(data) < HEADER_LENGTH:
            raise ValueError(f"Header too short: {len(data)}")
            
        version, type_, flags, stream_id, length = struct.unpack("!BBHII", data)
        return cls(version, type_, flags, stream_id, length)
