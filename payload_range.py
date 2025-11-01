from dataclasses import dataclass

from uint8 import UInt8


@dataclass(frozen=True)
class PayloadRange:
    low: UInt8
    high: UInt8

    def __post_init__(self):
        if self.low > self.high:
            raise ValueError(f"PayloadRange low={self.low} > high={self.high}")

    def contains(self, value: UInt8) -> bool:
        return self.low <= value <= self.high

