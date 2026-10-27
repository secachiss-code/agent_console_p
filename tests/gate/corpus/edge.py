import typing
import typing as t
x: "t.Any" = 1
y = getattr(typing, "cast")
class X(typing.Protocol):
    def m(self) -> int: ...
class Y(object):
    def m(self) -> int: ...
def z(): return None
def q(): return ...
