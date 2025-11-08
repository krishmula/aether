"""Unsigned 8-bit integer value object."""


class UInt8(int):
    """Integer constrained to the inclusive range 0..255."""

    __slots__ = ()

    def __new__(cls, value: int) -> "UInt8":
        if value < 0 or value > 255:
            raise ValueError("value must be in range 0..255")
        return int.__new__(cls, value)
