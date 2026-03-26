
from typing import Protocol, Any

class DuplexStream(Protocol):
    async def read(self, n: int = -1) -> bytes:
        ...
    
    async def readexactly(self, n: int) -> bytes:
        ...

    async def write(self, data: bytes) -> None:
        ...
        
    async def close(self) -> None:
        ...

class AsyncIOStreamAdapter:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        
    async def read(self, n: int = -1) -> bytes:
        return await self.reader.read(n)
        
    async def readexactly(self, n: int) -> bytes:
        return await self.reader.readexactly(n)
        
    async def write(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()
        
    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass
