
import asyncio
from typing import Optional
from .yamux.session import Session

class StreamPool:
    def __init__(self, session: Session, max_streams: int = 8, reuse: bool = True):
        self.session = session
        self.max_streams = max_streams
        self.reuse = reuse
        self.idle: list = []
        self.pending: list = [] # Waiters
        self.active_count = 0

    async def acquire(self):
        if self.reuse and self.idle:
            return self.idle.pop()

        if self.active_count < self.max_streams:
            self.active_count += 1
            return await self.session.open_stream()
        
        # Wait for a stream
        fut = asyncio.Future()
        self.pending.append(fut)
        return await fut

    def release(self, stream):
        if stream.closed or stream.remote_closed:
            self.active_count -= 1
            self._process_pending()
            return

        if self.reuse:
             # Logic to reset stream or just keep it open?
             # Yamux streams are persistent until closed.
             self.idle.append(stream)
             self._process_pending()
        else:
             asyncio.create_task(stream.close())
             self.active_count -= 1
             self._process_pending()

    def _process_pending(self):
        while self.pending and (self.idle or self.active_count < self.max_streams):
             fut = self.pending.pop(0)
             if self.idle:
                 fut.set_result(self.idle.pop())
             else:
                 # Must open new
                 asyncio.create_task(self._open_and_resolve(fut))

    async def _open_and_resolve(self, fut):
         self.active_count += 1
         stream = await self.session.open_stream()
         fut.set_result(stream)
