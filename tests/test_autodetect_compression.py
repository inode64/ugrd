from unittest import TestCase, main
from unittest.mock import Mock, patch

from ugrd.fs.cpio import autodetect_compression


class _FakeConfig(dict):
    """Minimal stand-in for InitramfsGenerator: dict + .get + .logger."""

    def __init__(self, **values):
        super().__init__(values)
        self.logger = Mock()


def _patch_support(kernel_supported, modules_available):
    """Patch the kernel-config and Python-module probes used by autodetect_compression.

    kernel_supported: dict mapping CONFIG_RD_* suffix -> True/False/None.
    modules_available: iterable of importable module names.
    """
    return patch.multiple(
        "ugrd.fs.cpio",
        _kernel_supports_decompressor=lambda self, kconfig: kernel_supported.get(kconfig),
        _python_compressor_available=lambda module: module in modules_available,
    )


class TestAutodetectCompression(TestCase):
    def test_auto_picks_first_supported(self):
        cfg = _FakeConfig(cpio_compression="auto")
        with _patch_support({"RD_XZ": True, "RD_ZSTD": True}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "xz")

    def test_auto_skips_unsupported_kernel(self):
        cfg = _FakeConfig(cpio_compression="auto")
        with _patch_support({"RD_XZ": False, "RD_ZSTD": True}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "zstd")

    def test_auto_falls_back_to_no_compression(self):
        cfg = _FakeConfig(cpio_compression="auto")
        with _patch_support({"RD_XZ": False, "RD_ZSTD": False}, set()):
            autodetect_compression(cfg)
        self.assertIs(cfg["cpio_compression"], False)

    def test_explicit_backend_kept_when_supported(self):
        cfg = _FakeConfig(cpio_compression="zstd")
        with _patch_support({"RD_XZ": True, "RD_ZSTD": True}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "zstd")

    def test_explicit_backend_falls_back_when_kernel_missing(self):
        """Forcing zstd on a kernel without CONFIG_RD_ZSTD falls back to xz."""
        cfg = _FakeConfig(cpio_compression="zstd")
        with _patch_support({"RD_XZ": True, "RD_ZSTD": False}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "xz")
        cfg.logger.warning.assert_any_call(
            "[cpio_compression] Kernel does not enable %s, skipping %s."
            % ("\x1b[33mCONFIG_RD_ZSTD\x1b[0m", "\x1b[33mzstd\x1b[0m")
        )

    def test_explicit_backend_falls_back_to_no_compression(self):
        """Forcing xz with no kernel support and no working alternatives → uncompressed."""
        cfg = _FakeConfig(cpio_compression="xz")
        with _patch_support({"RD_XZ": False, "RD_ZSTD": False}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertIs(cfg["cpio_compression"], False)

    def test_explicit_backend_falls_back_when_module_missing(self):
        """Forcing zstd without the zstandard module falls back to xz."""
        cfg = _FakeConfig(cpio_compression="zstd")
        with _patch_support({"RD_XZ": True, "RD_ZSTD": True}, {"lzma"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "xz")

    def test_true_alias_is_validated_like_xz(self):
        cfg = _FakeConfig(cpio_compression="true")
        with _patch_support({"RD_XZ": False, "RD_ZSTD": True}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "zstd")

    def test_false_string_is_passed_through(self):
        cfg = _FakeConfig(cpio_compression="false")
        with _patch_support({}, set()):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "false")

    def test_boolean_false_is_passed_through(self):
        cfg = _FakeConfig(cpio_compression=False)
        with _patch_support({}, set()):
            autodetect_compression(cfg)
        self.assertIs(cfg["cpio_compression"], False)

    def test_unknown_value_falls_back_to_supported(self):
        """An unsupported backend (e.g. lz4) falls back to the best available pycpio backend."""
        cfg = _FakeConfig(cpio_compression="lz4")
        with _patch_support({"RD_XZ": True, "RD_ZSTD": True}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "xz")
        cfg.logger.warning.assert_called()

    def test_unknown_value_falls_back_to_no_compression(self):
        """An unsupported backend with no working alternative falls back to uncompressed."""
        cfg = _FakeConfig(cpio_compression="lz4")
        with _patch_support({"RD_XZ": False, "RD_ZSTD": False}, set()):
            autodetect_compression(cfg)
        self.assertIs(cfg["cpio_compression"], False)

    def test_unverifiable_kernel_trusts_requested_backend(self):
        cfg = _FakeConfig(cpio_compression="zstd")
        with _patch_support({"RD_XZ": None, "RD_ZSTD": None}, {"lzma", "zstandard"}):
            autodetect_compression(cfg)
        self.assertEqual(cfg["cpio_compression"], "zstd")


if __name__ == "__main__":
    main()
