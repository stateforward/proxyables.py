import io
import typing

I = typing.Annotated[typing.TypeVar("I"), "the importated proxyable object type"]


class Proxyable:

    @classmethod
    def import_from(cls, stream: io.BytesIO) -> I:
        pass

    @classmethod
    def export_to(cls, *args, **kwargs):
        pass
