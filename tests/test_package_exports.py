from proxyables import (
    ExportedProxyable,
    ImportedProxyable,
    ObjectRegistry,
    ProxyCursor,
    Proxyable,
    StreamPool,
    decode,
    encode,
    make_muid,
)


def test_package_exports_surface():
    assert Proxyable.__name__ == "Proxyable"
    assert ExportedProxyable.__name__ == "ExportedProxyable"
    assert ImportedProxyable.__name__ == "ImportedProxyable"
    assert ProxyCursor.__name__ == "ProxyCursor"
    assert ObjectRegistry.__name__ == "ObjectRegistry"
    assert StreamPool.__name__ == "StreamPool"
    assert callable(encode)
    assert callable(decode)
    assert isinstance(make_muid(), str)
