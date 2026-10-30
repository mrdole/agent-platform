from checksum import checksum


def test_empty():
    assert checksum(b"") == 0


def test_bytes():
    assert checksum(bytes([1, 2, 3])) == 6


def test_wraps():
    assert checksum(bytes([200, 100])) == 44
