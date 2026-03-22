# Proxyables (Python)

A high-performance, peer-to-peer RPC library that makes remote objects feel local. Built on top of **Yamux** multiplexing and Python's `__getattr__` magic, it enables seamless bi-directional interaction between processes with support for callbacks, distributed garbage collection, and transparent async proxying.

## Features

- **Peer-to-Peer Architecture**: No strict client/server distinction — both sides can import and export objects, enabling true bi-directional communication.
- **"Local-Feeling" API**: Intercepts property access, method calls, and item access using `__getattr__` and `__getitem__`, making remote objects behave like local ones.
- **Distributed Garbage Collection**: Automatically manages remote object lifecycles using `weakref` and a reference counting protocol.
- **Bi-Directional Callbacks**: Pass functions and objects as arguments — they are automatically registered and hydrated as proxies on the other side.
- **Instruction Batching**: Chains of property accesses and method calls are accumulated and sent as a single `EXECUTE` instruction for efficiency.
- **Stream Multiplexing**: Uses Yamux to multiplex concurrent operations over a single connection.

## Installation

```bash
pip install proxyables
```

## Usage

### Basic Example

**Server (Exporting an object):**
```python
from proxyables import Proxyable

class API:
    def echo(self, msg: str) -> str:
        return f"echo {msg}"

    def compute(self, a: int, b: int) -> int:
        return a + b

# stream is any asyncio duplex stream
exported = await Proxyable.export(API(), stream)
```

**Client (Importing the object):**
```python
from proxyables import Proxyable

proxy = await Proxyable.import_from(stream)

# Usage - feels completely local!
result = await proxy.echo("hello")    # "echo hello"
result = await proxy.compute(10, 20)  # 30
```

## Architecture

1. **Proxy Layer**: `ProxyCursor` wraps remote objects, accumulating instructions on property access and method calls.
2. **Instruction Protocol**: Operations (get, apply, etc.) are serialized into `ProxyInstruction` messages using MessagePack.
3. **Transport**: Uses Yamux to multiplex concurrent operations over a single connection (TCP, Unix socket, stdio, etc.).
4. **Reference Management**: An `ObjectRegistry` tracks local objects passed by reference with automatic cleanup via `weakref`.

## License

MIT
