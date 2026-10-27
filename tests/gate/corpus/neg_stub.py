import abc
import typing as t
from abc import abstractmethod
from typing import Protocol, TypeVar, overload

T = TypeVar("T")


class P(Protocol):
    def m(self) -> int: ...
    def n(self) -> int:
        """doc"""


class P2(t.Protocol[T]):
    def m(self) -> T: ...


class P3(P):
    def q(self) -> int: ...


class P4(t.Protocol):
    def z(self) -> int: ...


class A(ABC if False else object):
    @abstractmethod
    def m(self) -> int: ...

    @abc.abstractmethod
    def n(self) -> int:
        """doc"""

    @t.overload
    def o(self, x: int) -> int: ...

    @overload
    def o(self, x: str) -> str: ...

    def real(self) -> int:
        return 1

    def raise_other(self):
        raise ValueError("not a placeholder")

    def pass_then_code(self):
        pass
        return 1
