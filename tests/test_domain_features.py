
import pytest
import asyncio
from typing import Any, List, Dict
from proxyables.proxyable import Proxyable
from proxyables.transport import AsyncIOStreamAdapter

# Setup Infrastructure
class HelperServer:
    def __init__(self, target_object):
        self.target = target_object
        self.server = None

    async def start(self, port=0):
        running_event = asyncio.Event()
        
        async def handle_conn(reader, writer):
            stream = AsyncIOStreamAdapter(reader, writer)
            # Export and keep running
            await Proxyable.export(self.target, stream)
            
        self.server = await asyncio.start_server(handle_conn, '127.0.0.1', port)
        addr = self.server.sockets[0].getsockname()
        self.port = addr[1]
        asyncio.create_task(self.server.serve_forever())
        return self.port

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

class HelperClient:
    def __init__(self, port):
        self.port = port
        self.writer = None

    async def connect(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        self.writer = writer
        stream = AsyncIOStreamAdapter(reader, writer)
        return await Proxyable.import_from(stream)

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

import pytest_asyncio

@pytest_asyncio.fixture
async def proxy_pair():
    # Tests will override target by calling setup_target? 
    # Or strict fixture?
    # Better to have a factory.
    servers = []
    clients = []

    async def factory(target_object):
        server = HelperServer(target_object)
        port = await server.start()
        servers.append(server)
        
        client = HelperClient(port)
        proxy = await client.connect()
        clients.append(client)
        return proxy

    yield factory

    for c in clients: await c.close()
    for s in servers: await s.stop()


# --- Domain Tests ---

# 1. Callbacks
@pytest.mark.asyncio
async def test_domain_callbacks(proxy_pair):
    class CallbackHost:
        def run_callback(self, cb, arg):
            # args might be hydrated proxies
            if asyncio.iscoroutinefunction(cb) or asyncio.iscoroutine(cb):
                # We can't easily check iscoroutinefunction on a Proxy object
                # But we can await it
                pass
            # Server calling Client function
            return cb(arg) # Should flow back to client

    proxy = await proxy_pair(CallbackHost())

    # Client side callback
    def my_callback(val):
        return f"Client received {val}"

    # Pass generic python function
    # Note: simple functions might not be async, but Proxy call is async.
    # The hydration infrastructure makes the proxy call async on the other side.
    # But `cb(arg)` on server returns a Coroutine (Promise) if it's a proxy?
    # No, `ImportedProxyable` (which wraps the callback on server) `__call__` returns a `ProxyCursor`.
    # `ProxyCursor` is Awaitable.
    # So `cb(arg)` returns an awaitable.
    # The server (CallbackHost) needs to await it if it wants the result!
    
    class AsyncCallbackHost:
        async def run_callback(self, cb, arg):
            # Check if cb is proxy (it is)
            res = cb(arg)
            # It returns a proxy cursor (awaitable)
            return await res

    proxy = await proxy_pair(AsyncCallbackHost())
    
    result = await proxy.run_callback(my_callback, "Test")
    assert result == "Client received Test"


# 2. Classes (Construction)
@pytest.mark.asyncio
async def test_domain_classes(proxy_pair):
    class Greeter:
        def __init__(self, greeting):
            self.greeting = greeting
        
        def greet(self, name):
            return f"{self.greeting} {name}"

    class ExportRoot:
        def __init__(self):
            self.Greeter = Greeter

    proxy = await proxy_pair(ExportRoot())
    
    # In Python, calling a class constructs it.
    # proxy.Greeter is a proxy to the class object.
    # proxy.Greeter("Hello") sends APPLY.
    # Server executes Greeter("Hello") -> returns instance.
    # Instance is registered and Reference returned.
    
    greeter_instance = await proxy.Greeter("Hello")
    
    # Verify instance method
    msg = await greeter_instance.greet("World")
    assert msg == "Hello World"


# 3. Arrays
@pytest.mark.asyncio
async def test_domain_arrays(proxy_pair):
    class ArrayHost:
        def __init__(self):
            self.list = [10, 20, 30]
            self.dict = {"a": 1, "b": 2}
            
        def get_item(self, idx):
            return self.list[idx]

    proxy = await proxy_pair(ArrayHost())
    
    # Access list property. It should be a proxy (Reference).
    # Because we updated `_create_value` to register lists.
    
    # Access index
    # proxy.list returns ProxyCursor
    # proxy.list[1] returns ProxyCursor
    # await it
    
    # Currently `ProxyCursor` doesn't implement `__getitem__`.
    # `imported.py` `__getattr__` uses `create_get_instruction(name)`.
    # `__getattr__` only handles dot access usually.
    # We need to implement `__getitem__` in `ProxyCursor` to support `[0]`.
    
    # I will assert that this FAILS currently, or fix it in imported.py first?
    # Better to fix it. I'll write the test assuming I will fix imported.py next.
    
    # Assuming __getitem__ works:
    val = await proxy.list[1]
    assert val == 20
    
    # Length? __len__ is hard to proxy in Python async.
    # But fetching length via property?
    # Python lists don't have .length property. they use len().
    # proxy.list.__len__()? No, `len(proxy.list)` calls `__len__` synchronously.
    # This won't work for async proxy.
    # User has to skip len check or expose it as method.
    
    # Test dictionary access
    val_a = await proxy.dict["a"]
    assert val_a == 1


# 4. Error Propagation
@pytest.mark.asyncio
async def test_domain_errors(proxy_pair):
    class ErrorHost:
        def fail(self):
            raise ValueError("Boom")

    proxy = await proxy_pair(ErrorHost())
    
    with pytest.raises(Exception, match="Boom"):
        await proxy.fail()


# 5. Nested / Immutability
@pytest.mark.asyncio
async def test_domain_nested(proxy_pair):
    class Inner:
        def __init__(self):
            self.x = 100
            
    class Outer:
        def __init__(self):
            self.inner = Inner()
            self.y = 50

    proxy = await proxy_pair(Outer())
    
    # Access nested
    inner = await proxy.inner
    x = await inner.x
    assert x == 100
    
    # Seperate chain
    inner2 = proxy.inner
    # Identity might be different for proxies, but pointing to same ref
    # We just want to ensure it works.
    x2 = await inner2.x
    assert x2 == 100


# 6. Null / Undefined
@pytest.mark.asyncio
async def test_domain_nulls(proxy_pair):
    class NullHost:
        def __init__(self):
            self.none_val = None
            
        def get_none(self):
            return None

    proxy = await proxy_pair(NullHost())
    
    val = await proxy.none_val
    assert val is None
    
    res = await proxy.get_none()
    assert res is None

