
import asyncio
import struct
from typing import Dict, Optional, Callable
from .consts import (
    PROTOCOL_VERSION, TYPE_DATA, TYPE_WINDOW_UPDATE, TYPE_PING, TYPE_GO_AWAY,
    FLAG_SYN, FLAG_ACK, FLAG_FIN, FLAG_RST, HEADER_LENGTH
)
from .frame import Frame
from .stream import Stream
from ..transport import DuplexStream

class Session:
    def __init__(self, stream: DuplexStream, is_client: bool):
        self.stream = stream
        self.is_client = is_client
        
        # Client initiates weird IDs (1, 3, 5...), Server evens (2, 4, 6...)
        self.next_stream_id = 1 if is_client else 2
        
        self.streams: Dict[int, Stream] = {}
        self.write_lock = asyncio.Lock()
        self.running = False
        self.accept_queue = asyncio.Queue() # For incoming streams
        
    async def start(self):
        self.running = True
        asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            while self.running:
                # Read Header
                try:
                    header_bytes = await self.stream.readexactly(HEADER_LENGTH)
                except asyncio.IncompleteReadError:
                    break # Connection closed
                except Exception:
                    break
                
                frame = Frame.decode_header(header_bytes)
                
                #Read Payload
                payload = b""
                if frame.length > 0:
                    try:
                        payload = await self.stream.readexactly(frame.length)
                    except asyncio.IncompleteReadError:
                         break
                    frame.payload = payload
                
                await self._handle_frame(frame)
        except Exception as e:
            # print(f"Session read loop error: {e}")
            pass
        finally:
            await self.close()

    async def _handle_frame(self, frame: Frame):
        stream = self.streams.get(frame.stream_id)
        
        if frame.flags & FLAG_SYN:
            # New Stream
            if stream: 
                # Duplicate SYN? Protocol error?
                pass
            else:
                stream = Stream(self, frame.stream_id)
                self.streams[frame.stream_id] = stream
                await self.accept_queue.put(stream)
        
        if stream:
            if frame.type == TYPE_DATA:
                if frame.length > 0:
                    stream.feed_data(frame.payload)
                if frame.flags & FLAG_FIN:
                    stream.on_fin()
            
            # TODO: WindowUpdate handling
            
            if frame.flags & FLAG_RST:
                stream.on_fin() # Treat reset as close for now
                del self.streams[frame.stream_id]

    async def open_stream(self) -> Stream:
        stream_id = self.next_stream_id
        self.next_stream_id += 2
        
        stream = Stream(self, stream_id)
        self.streams[stream_id] = stream
        
        # Send SYN
        await self.write_frame(TYPE_DATA, FLAG_SYN, stream_id)
        return stream

    async def accept_stream(self) -> Stream:
        return await self.accept_queue.get()

    async def write_frame(self, type: int, flags: int, stream_id: int, payload: bytes = b""):
        frame = Frame(
            version=PROTOCOL_VERSION,
            type=type,
            flags=flags,
            stream_id=stream_id,
            length=len(payload),
            payload=payload
        )
        data = frame.encode()
        async with self.write_lock:
            await self.stream.write(data)

    async def close(self):
        if not self.running:
             return
        self.running = False
        try:
            await self.stream.close()
        except:
            pass
        # Clean up streams
        for s in list(self.streams.values()):
            s.on_fin()
        self.streams.clear()
