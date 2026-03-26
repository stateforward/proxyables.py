
import asyncio
import inspect
from typing import Any, Optional, List, Dict
import msgpack
from .yamux.session import Session
from .registry import ObjectRegistry
from .types import (
    ProxyInstruction, ProxyInstructionKinds, ProxyValueKinds, 
    ProxyError, ProxyInstruction
)
from .instructions import (
    create_return_instruction, create_throw_instruction, 
    create_instruction_unsafe
)
from .encoder import encode
from .decoder import decode, decode_hook
from .muid import make as muid

from .imported import ImportedProxyable, ProxyCursor


async def read_message(stream):
    unpacker = msgpack.Unpacker(raw=False, object_hook=decode_hook)
    while True:
        for item in unpacker:
            return item
        chunk = await stream.read()
        if not chunk:
            if unpacker.tell() == 0:
                return b""
            raise BrokenPipeError("Incomplete request")
        unpacker.feed(chunk)

class ExportedProxyable:
    def __init__(self, session: Session, obj: Any, registry: Optional[ObjectRegistry] = None):
        self.session = session
        self.root_object = obj
        self.registry = registry or ObjectRegistry()
        self.running = False
        self.importer = ImportedProxyable(session, listen=False)

    async def start(self):
        self.running = True
        asyncio.create_task(self._accept_loop())

    async def _accept_loop(self):
        while self.running:
            stream = await self.session.accept_stream()
            asyncio.create_task(self._handle_stream(stream))

    async def _handle_stream(self, stream):
        # Read request
        try:
            data = await read_message(stream)
            if not data:
                return 
            
            instruction = data if isinstance(data, ProxyInstruction) else decode(data)
            # Should be EXECUTE kind
            result = await self._execute(instruction)
            
            # Send result
            resp = encode(result)
            await stream.write(resp)
            await stream.close()
        except Exception as e:
            # print(f"Exported stream error: {e}")
            try:
                err = create_throw_instruction(ProxyError(message=str(e)))
                await stream.write(encode(err))
                await stream.close()
            except:
                pass

    async def _execute(self, instruction: ProxyInstruction) -> ProxyInstruction:
        if instruction.kind != ProxyInstructionKinds.EXECUTE:
            return create_throw_instruction(ProxyError("Expected EXECUTE instruction"))
        
        stack = []
        instructions: List[ProxyInstruction] = instruction.data
        
        # Start with root object as current target context?
        # Actually in TS, `current_target` is determined by stack. 
        # Default root if stack empty?
        # Stack flow:
        # [Ref] -> Target = Ref
        # [Ref, Get(A)] -> Target = Ref.A
        # [Ref, Get(A), Apply] -> Target = Ref.A()
        
        # We need to maintain a "current value" or stack of values.
        # TS implementation pushes results to stack.
        
        for instr in instructions:
            try:
                if instr.kind == ProxyValueKinds.REFERENCE:
                    stack.append(instr)
                    continue
                
                target = self.root_object
                # Resolve target from stack if present
                if stack:
                    top = stack[-1]
                    if top.kind == ProxyValueKinds.REFERENCE:
                        ref_id = top.data
                        target = self.registry.get(ref_id)
                        if target is None:
                             return create_throw_instruction(ProxyError(f"Object {ref_id} not found"))
                        stack.pop() # Consume reference?
                        # In TS implementation: "Let's assume `get` consumes the subject from stack." -> Yes.
                    elif top.kind == ProxyInstructionKinds.RETURN:
                        # Chained operations on result?
                        # Usually RETURN wraps UnproxyableValue.
                        pass # TODO

                result = await self._process_instruction(instr, target)
                stack.append(result)
            except Exception as e:
                return create_throw_instruction(ProxyError(message=str(e)))

        if not stack:
             return create_return_instruction(None)
             
        # Pop final result
        final = stack.pop()
        # If it's a raw value, wrap in RETURN?
        # Usually instructions return ProxyInstruction (RETURN/THROW/VAL)
        # We should ensure we return a RETURN instruction.
        if final.kind != ProxyInstructionKinds.RETURN and final.kind != ProxyInstructionKinds.THROW:
             return create_return_instruction(final)
        
        return final

    async def _process_instruction(self, instr: ProxyInstruction, target: Any) -> ProxyInstruction:
        if instr.kind == ProxyInstructionKinds.GET:
            key = instr.data[0]
            if isinstance(target, (list, tuple)) and isinstance(key, (int, float)):
                 # Handle implementation details of list access
                 try:
                     val = target[int(key)]
                 except IndexError:
                     val = None # JS returns undefined
            elif isinstance(target, dict):
                 val = target.get(key)
            else:
                 val = getattr(target, key, None)
            return self._create_value(val)
            
        elif instr.kind == ProxyInstructionKinds.APPLY:
            args = instr.data 
            
            hydrated_args = [self._hydrate(a) for a in args]
            
            if inspect.iscoroutinefunction(target):
                res = await target(*hydrated_args)
            else:
                res = target(*hydrated_args)
            
            return self._create_value(res)
            
        elif instr.kind == ProxyInstructionKinds.CONSTRUCT:
            args = instr.data
            hydrated_args = [self._hydrate(a) for a in args]
            
            # Construct assuming target is a class
            res = target(*hydrated_args)
            return self._create_value(res)

        elif instr.kind == ProxyInstructionKinds.RELEASE:
             ref_id = instr.data[0]
             self.registry.delete(ref_id)
             return self._create_value(None)
             
        else:
            raise ValueError(f"Unknown instruction kind: {instr.kind}")

    def _create_value(self, value: Any) -> ProxyInstruction:
        # Check if primitive
        if value is None or isinstance(value, (int, float, str, bool)):
             # Return directly
             # Wait, UnproxyableValue wrapper?
             # TS: createValue returns `UnproxyableValue` which has {id, kind, data}.
             # We should probably return {kind=Number, data=123}.
             # But `_process_instruction` should return `Instruction`?
             # Stack expects Instructions.
             # So we wrap it in a pseudo-Instruction or just dictionary?
             # The final result is wrapped in RETURN.
             # Stack items are just intermediate values.
             # Actually, stack items ARE `ProxyInstruction`s in TS.
             pass
        
        # If object/function, register and return Reference
        # Arrays and Dicts are proxied now
        if callable(value) or hasattr(value, '__dict__') or isinstance(value, (list, dict, tuple)):
             ref_id = self.registry.register(value)
             return ProxyInstruction(
                 kind=ProxyValueKinds.REFERENCE,
                 data=ref_id,
                 id=muid()
             )

        # Fallback for primitives
        # Fallback for primitives
        return ProxyInstruction(
             kind=self._infer_kind(value),
             data=value,
             id=muid()
        )

    def _infer_kind(self, val):
        if val is None: return ProxyValueKinds.NULL
        if isinstance(val, bool): return ProxyValueKinds.BOOLEAN
        if isinstance(val, int): return ProxyValueKinds.NUMBER
        if isinstance(val, float): return ProxyValueKinds.NUMBER
        if isinstance(val, str): return ProxyValueKinds.STRING
        return ProxyValueKinds.UNKNOWN

    def _hydrate(self, arg):
        # Check if arg is a Reference from client (Server calling Client Callback)
        if isinstance(arg, (dict, ProxyInstruction)):
            # Normalize to dict access or object
            kind = getattr(arg, 'kind', arg.get('kind') if isinstance(arg, dict) else None)
            data = getattr(arg, 'data', arg.get('data') if isinstance(arg, dict) else None)
            
            if kind == ProxyValueKinds.REFERENCE:
                # Create a proxy cursor using the importer
                # We need to construct a valid instruction list starting with this reference
                ref_instr = ProxyInstruction(
                    kind=ProxyValueKinds.REFERENCE,
                    data=data,
                    id=muid()
                )
                return self.importer.create_proxy([ref_instr])
            
            # Unwrap primitives
            return data
                
        return arg 
