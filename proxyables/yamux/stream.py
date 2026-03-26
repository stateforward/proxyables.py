
import asyncio
from typing import Optional, TYPE_CHECKING
from .consts import TYPE_DATA, FLAG_FIN, FLAG_ACK, FLAG_SYN

if TYPE_CHECKING:
    from .session import Session

class Stream:
    def __init__(self, session: "Session", stream_id: int):
        self.session = session
        self.stream_id = stream_id
        self.read_queue = asyncio.Queue()
        self.closed = False
        self.remote_closed = False
    
    async def read(self) -> bytes:
        if self.read_queue.empty() and self.remote_closed:
             return b""
        
        chunk = await self.read_queue.get()
        if chunk is None: # EOF marker
            return b""
        return chunk
    
    async def write(self, data: bytes):
        if self.closed:
            raise BrokenPipeError("Stream closed locally")
        if self.remote_closed:
            raise BrokenPipeError("Stream closed remotely")
        
        await self.session.write_frame(
            type=TYPE_DATA,
            flags=0,
            stream_id=self.stream_id,
            payload=data
        )
    
    async def close(self):
        if self.closed:
            return
        self.closed = True
        # Send FIN
        await self.session.write_frame(
            type=TYPE_DATA, # Yamux sends FIN on Data frame usually or WindowUpdate? Spec says Data/WindowUpdate.
            flags=FLAG_FIN, 
            stream_id=self.stream_id
        )
         # Also put EOF in read queue in case we were waiting?
        await self.read_queue.put(None)

    def feed_data(self, data: bytes):
        self.read_queue.put_nowait(data)
    
    def on_fin(self):
        self.remote_closed = True
        self.read_queue.put_nowait(None)
