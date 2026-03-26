
from .types import (
    ProxyInstruction, ProxyInstructionKinds, ProxyError, ProxyValueKinds
)
from .muid import make as muid
from typing import Any, List, Optional

def create_instruction_unsafe(kind: int, data: Any) -> ProxyInstruction:
    return ProxyInstruction(
        id=muid(),
        kind=kind,
        data=data
    )

def create_get_instruction(key: str) -> ProxyInstruction:
    return create_instruction_unsafe(ProxyInstructionKinds.GET, [key])

def create_apply_instruction(args: List[Any]) -> ProxyInstruction:
    return create_instruction_unsafe(ProxyInstructionKinds.APPLY, args)

def create_construct_instruction(args: List[Any]) -> ProxyInstruction:
    return create_instruction_unsafe(ProxyInstructionKinds.CONSTRUCT, args)

def create_return_instruction(value: Any) -> ProxyInstruction:
    return create_instruction_unsafe(ProxyInstructionKinds.RETURN, value)

def create_throw_instruction(error: ProxyError) -> ProxyInstruction:
    return create_instruction_unsafe(ProxyInstructionKinds.THROW, error)

def create_release_instruction(ref_id: str) -> ProxyInstruction:
    return create_instruction_unsafe(ProxyInstructionKinds.RELEASE, [ref_id])

def create_execute_instruction(instructions: List[ProxyInstruction]) -> ProxyInstruction:
    return create_instruction_unsafe(ProxyInstructionKinds.EXECUTE, instructions)
