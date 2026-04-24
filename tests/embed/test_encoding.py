"""Unit tests for vector encoding and hash_seq construction."""

import struct
import pytest

from kairix.embed.embed import build_hash_seq, encode_vector


@pytest.mark.unit
class TestEncodeVector:
    @pytest.mark.unit
    def test_single_float(self):
        result = encode_vector([1.0])
        assert result == struct.pack("<1f", 1.0)

    @pytest.mark.unit
    def test_1536_dims(self):
        vec = [0.1] * 1536
        result = encode_vector(vec)
        assert len(result) == 1536 * 4  # 4 bytes per float32

    @pytest.mark.unit
    def test_little_endian(self):
        # sqlite-vec expects little-endian
        vec = [1.0, 2.0, 3.0]
        result = encode_vector(vec)
        assert result == struct.pack("<3f", 1.0, 2.0, 3.0)

    @pytest.mark.unit
    def test_zero_vector(self):
        vec = [0.0] * 10
        result = encode_vector(vec)
        assert result == bytes(40)

    @pytest.mark.unit
    def test_negative_values(self):
        vec = [-0.5, 0.5]
        result = encode_vector(vec)
        expected = struct.pack("<2f", -0.5, 0.5)
        assert result == expected

    @pytest.mark.unit
    def test_roundtrip(self):
        original = [0.123456, -0.654321, 1.0, -1.0]
        encoded = encode_vector(original)
        decoded = list(struct.unpack(f"<{len(original)}f", encoded))
        for a, b in zip(original, decoded, strict=False):
            assert abs(a - b) < 1e-5  # float32 precision


@pytest.mark.unit
class TestBuildHashSeq:
    @pytest.mark.unit
    def test_basic(self):
        assert build_hash_seq("abc123", 0) == "abc123_0"

    @pytest.mark.unit
    def test_seq_1(self):
        assert build_hash_seq("abc123", 1) == "abc123_1"

    @pytest.mark.unit
    def test_matches_qmd_format(self):
        # QMD uses: cv.hash || '_' || cv.seq
        h = "deadbeef"
        for seq in range(5):
            assert build_hash_seq(h, seq) == f"{h}_{seq}"

    @pytest.mark.unit
    def test_long_hash(self):
        h = "a" * 64  # SHA256
        assert build_hash_seq(h, 3) == f"{'a' * 64}_3"
