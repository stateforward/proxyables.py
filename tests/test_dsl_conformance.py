from proxyables.instructions import (
    create_apply_instruction,
    create_construct_instruction,
    create_execute_instruction,
    create_get_instruction,
    create_release_instruction,
)
from proxyables.types import ProxyInstructionKinds, ProxyValueKinds


def test_dsl_constants_match_shared_contract():
    assert ProxyValueKinds.REFERENCE == 0x5A1B3C4D
    assert ProxyInstructionKinds.EXECUTE == 0xA01E3D98
    assert ProxyInstructionKinds.RELEASE == 0x1A2B3C4D


def test_dsl_instruction_shapes_are_canonical():
    assert create_get_instruction("key").data == ["key"]
    assert create_apply_instruction([1, 2]).data == [1, 2]
    assert create_construct_instruction([1, 2]).data == [1, 2]
    assert create_release_instruction("ref-1").data == ["ref-1"]
    assert create_execute_instruction([create_get_instruction("key")]).kind == ProxyInstructionKinds.EXECUTE
