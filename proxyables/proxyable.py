
from typing import Any
from .yamux.session import Session
from .exported import ExportedProxyable
from .imported import ImportedProxyable
from .transport import DuplexStream

class Proxyable:
    @staticmethod
    async def export(obj: Any, stream: DuplexStream):
        # Create Yamux Session
        session = Session(stream, is_client=False)
        await session.start()
        
        exported = ExportedProxyable(session, obj)
        await exported.start()
        return exported

    @staticmethod
    async def import_from(stream: DuplexStream):
        # Create Yamux Session 
        session = Session(stream, is_client=True)
        await session.start()
        
        imported = ImportedProxyable(session)
        # Return proxy cursor
        return imported.create_proxy()
