
import asyncio
import pytest
from proxyables.proxyable import Proxyable
from proxyables.yamux.session import Session
from proxyables.transport import AsyncIOStreamAdapter

# Mock transport
async def create_connected_sessions():
    pass 
    
class TargetObject:
    def __init__(self):
        self.value = 100
        
    def add(self, a, b):
        return a + b
        
    async def async_add(self, a, b):
        await asyncio.sleep(0.01)
        return a + b
        
    def get_child(self):
        return ChildObject()

class ChildObject:
    def greet(self, name):
        return f"Hello {name}"

@pytest.mark.asyncio
async def test_e2e_rpc():
    # Setup verify E2E 
    
    # 1. Start Server
    async def handle_server_conn(reader, writer):
        target = TargetObject()
        stream = AsyncIOStreamAdapter(reader, writer)
        exported = await Proxyable.export(target, stream)
        # Keep running? Exported starts loops.
        
    server = await asyncio.start_server(handle_server_conn, '127.0.0.1', 8889)
    asyncio.create_task(server.serve_forever())
    
    # 2. Start Client
    reader, writer = await asyncio.open_connection('127.0.0.1', 8889)
    stream = AsyncIOStreamAdapter(reader, writer)
    proxy = await Proxyable.import_from(stream)
    
    # 3. Test interactions
    
    # Simple call
    res = await proxy.add(10, 20)
    assert res == 30
    
    # Async call logic on server
    res_async = await proxy.async_add(5, 5)
    assert res_async == 10
    
    # Get property
    val = await proxy.value
    assert val == 100
    
    # Nested object (Reference)
    child = await proxy.get_child()
    # verify child is a proxy cursor (has await)
    greeting = await child.greet("World")
    assert greeting == "Hello World"
    
    # Cleanup
    writer.close()
    await writer.wait_closed()
    server.close()
