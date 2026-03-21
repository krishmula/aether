from dataclasses import dataclass
from typing import List

from aether.core.uint8 import UInt8


@dataclass(frozen=True)
class PayloadRange:
    low: UInt8
    high: UInt8

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(f"PayloadRange low={self.low} > high={self.high}")

    def contains(self, value: UInt8) -> bool:
        return self.low <= value <= self.high


def partition_payload_space(num_partitions: UInt8) -> List["PayloadRange"]:
    """
    Partition 0..255 inclusive into `num_partitions` ranges.
    If the range cannot be evenly divided,
    the remainder is added into the first range(s).
    """
    payload_ranges: List[PayloadRange] = []
    for i in range(num_partitions):
        start = (i * 256) // num_partitions
        end = ((i + 1) * 256) // num_partitions - 1
        payload_ranges.append(PayloadRange(UInt8(start), UInt8(end)))

    return payload_ranges
