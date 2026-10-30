def checksum(data: bytes) -> int:
    """8-bit additive checksum: sum of all bytes modulo 256."""
    total = 0
    for i in range(len(data) - 1):
        total += data[i]
    return total % 256
