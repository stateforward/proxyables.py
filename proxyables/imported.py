
import asyncio
from typing import Any, List, Optional
import inspect
import msgpack
from .types import (
    ProxyInstruction, ProxyInstructionKinds, ProxyValueKinds, ProxyError
)
from .instructions import (
    create_get_instruction, create_apply_instruction, create_execute_instruction,
    create_release_instruction, create_return_instruction, create_throw_instruction
)
from .yamux.session import Session
from .stream_pool import StreamPool
from .encoder import encode
from .decoder import decode, decode_hook
from .muid import make as muid
from .registry import ObjectRegistry
import weakref


async def read_message(stream):
    unpacker = msgpack.Unpacker(raw=False, object_hook=decode_hook)
    while True:
        for item in unpacker:
            return item
        chunk = await stream.read()
        if not chunk:
            if unpacker.tell() == 0:
                return b""
            raise BrokenPipeError("Incomplete response")
        unpacker.feed(chunk)

class ImportedProxyable:
    def __init__(self, session: Session, listen: bool = True):
        self.session = session
        self.stream_pool = StreamPool(session, reuse=False)
        self.registry = ObjectRegistry()
        self.running = True
        if listen:
            asyncio.create_task(self._accept_loop())

    async def _accept_loop(self):
        try:
             while self.running:
                stream = await self.session.accept_stream()
                asyncio.create_task(self._handle_stream(stream))
        except Exception as e:
            pass

    async def _handle_stream(self, stream):
        try:
            data = await read_message(stream)
            if not data:
                return 
            
            instruction = data if isinstance(data, ProxyInstruction) else decode(data)
            # Support execute and release
            result = await self._execute_incoming(instruction)
            
            resp = encode(result)
            await stream.write(resp)
            await stream.close()
        except Exception as e:
            try:
                err = create_throw_instruction(ProxyError(message=str(e)))
                await stream.write(encode(err))
                await stream.close()
            except:
                pass
    
    async def _execute_incoming(self, instruction: ProxyInstruction):
        if instruction.kind == ProxyInstructionKinds.RELEASE:
             ref_id = instruction.data[0]
             self.registry.delete(ref_id)
             return create_return_instruction(None)

        if instruction.kind != ProxyInstructionKinds.EXECUTE:
             return create_throw_instruction(ProxyError("Expected EXECUTE instruction"))
        
        stack = []
        instructions: List[ProxyInstruction] = instruction.data
        
        for instr in instructions:
            if instr.kind == ProxyValueKinds.REFERENCE:
                stack.append(instr)
                continue
            
            # Resolve target
            target = None
            if stack:
                 top = stack.pop()
                 if top.kind == ProxyValueKinds.REFERENCE:
                     target = self.registry.get(top.data)
            
            if target is None:
                 return create_throw_instruction(ProxyError("Target not found"))
                 
            # EXECUTE usually only has APPLY/GET on target?
            if instr.kind == ProxyInstructionKinds.APPLY:
                args = instr.data
                # Hydrate args (if server passed references)
                hydrated_args = [self._hydrate(a) for a in args]
                
                if inspect.iscoroutinefunction(target):
                    res = await target(*hydrated_args)
                else:
                    res = target(*hydrated_args)
                
                # Result might be object, need to register? 
                # Use create_value
                val_instr = self._create_value(res)
                stack.append(val_instr)
            else:
                 return create_throw_instruction(ProxyError(f"Instruction {instr.kind} not supported on callback"))
                 
        if not stack:
             return create_return_instruction(None)
        
        final = stack.pop()
        if final.kind != ProxyInstructionKinds.RETURN and final.kind != ProxyInstructionKinds.THROW:
             return create_return_instruction(final)
        return final

    def _hydrate(self, arg):
        if isinstance(arg, (dict, ProxyInstruction)):
             kind = getattr(arg, 'kind', arg.get('kind') if isinstance(arg, dict) else None)
             data = getattr(arg, 'data', arg.get('data') if isinstance(arg, dict) else None)
             if kind == ProxyValueKinds.REFERENCE:
                  val_instr = ProxyInstruction(kind=ProxyValueKinds.REFERENCE, data=data, id=muid())
                  return ProxyCursor(self, [val_instr])
             # Unwrap primitives
             return data
        return arg

    def _create_value(self, value):
        if value is None or isinstance(value, (int, float, str, bool)):
             return ProxyInstruction(kind=self._infer_kind(value), data=value, id=muid())
        
        if callable(value) or hasattr(value, '__dict__') or isinstance(value, (list, dict, tuple)):
             ref_id = self.registry.register(value)
             return ProxyInstruction(
                 kind=ProxyValueKinds.REFERENCE,
                 data=ref_id,
                 id=muid()
             )
        return ProxyInstruction(kind=ProxyValueKinds.UNKNOWN, data=str(value), id=muid())

    def _infer_kind(self, val):
        if val is None: return ProxyValueKinds.NULL
        if isinstance(val, bool): return ProxyValueKinds.BOOLEAN
        if isinstance(val, int): return ProxyValueKinds.NUMBER
        if isinstance(val, float): return ProxyValueKinds.NUMBER
        if isinstance(val, str): return ProxyValueKinds.STRING
        return ProxyValueKinds.UNKNOWN

    def create_proxy(self, instructions: List[ProxyInstruction] = None):
        return ProxyCursor(self, instructions or [])

class ProxyCursor:
    def __init__(self, importer: ImportedProxyable, instructions: List[ProxyInstruction]):
        self._importer = importer
        self._instructions = instructions

    def __getattr__(self, name: str):
        new_instr = self._instructions + [create_get_instruction(name)]
        return ProxyCursor(self._importer, new_instr)

    def __getitem__(self, key: Any):
        new_instr = self._instructions + [create_get_instruction(key)]
        return ProxyCursor(self._importer, new_instr)

    def __call__(self, *args, **kwargs):
        # Serialize args
        processed_args = [self._importer._create_value(a) for a in args]
        
        new_instr = self._instructions + [create_apply_instruction(processed_args)]
        return ProxyCursor(self._importer, new_instr)

    def __await__(self):
        return self._execute().__await__()

    async def _execute(self):
        # Create EXECUTE instruction wrapping all accumulated instructions
        exec_instr = create_execute_instruction(self._instructions)
        
        # Acquire stream
        stream = await self._importer.stream_pool.acquire()
        try:
            # Send
            await stream.write(encode(exec_instr))
            
            # Receive
            data = await read_message(stream)
            if not data:
                raise BrokenPipeError("Empty response")
                
            res_instr = data if isinstance(data, ProxyInstruction) else decode(data) # Should be RETURN or THROW
            
            if res_instr.kind == ProxyInstructionKinds.THROW:
                 # Re-raise error
                 # ProxyError data?
                 err_data = res_instr.data
                 raise Exception(err_data.get('message', 'Unknown error') if isinstance(err_data, dict) else str(err_data))
            
            if res_instr.kind == ProxyInstructionKinds.RETURN:
                # Unwrap value
                val = res_instr.data
                # Check if it is a Reference
                # val is {kind, data, id} ??
                # Decoder returns ProxyInstruction object if matching struct.
                # Or dict if generic. 
                # If `val` is ProxyInstruction (Reference), we hydrate it.
                if isinstance(val, ProxyInstruction) and val.kind == ProxyValueKinds.REFERENCE:
                     # Create new ProxyCursor starting with this Reference
                     ref_cursor = ProxyCursor(self._importer, [val])
                     # Register GC
                     self._register_gc(ref_cursor, val.data)
                     return ref_cursor
                
                return val.data if isinstance(val, ProxyInstruction) else val
                
                # Check for dict result with kind? 
                if isinstance(val, dict) and 'kind' in val and val['kind'] == ProxyValueKinds.REFERENCE:
                      val_instr = ProxyInstruction(kind=ProxyValueKinds.REFERENCE, data=val['data'], id=val.get('id'))
                      ref_cursor = ProxyCursor(self._importer, [val_instr])
                      self._register_gc(ref_cursor, val_instr.data)
                      return ref_cursor

        finally:
            self._importer.stream_pool.release(stream)

    def _register_gc(self, proxy_obj, ref_id):
        # We need to send RELEASE when proxy_obj is GC'd.
        # Use weakref.finalize
        weakref.finalize(proxy_obj, self._release_remote, self._importer.session, ref_id)

    @staticmethod
    def _release_remote(session, ref_id):
        # This runs in GC callback, possibly unsure context.
        pass 
