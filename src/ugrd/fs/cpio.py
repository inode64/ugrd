__author__ = "desultory"
__version__ = "3.10.0"

from importlib import import_module
from os import uname
from pathlib import Path

from pycpio.cpio.symlink import CPIO_Symlink
from zenlib.util import colorize, contains, unset


# Compression candidates, ordered from best ratio to worst.
# Each entry: (cpio_compression value, kernel CONFIG_RD_* suffix, Python module name)
# Only backends supported by pycpio are listed.
_COMPRESSION_CANDIDATES = [
    ("xz", "RD_XZ", "lzma"),
    ("zstd", "RD_ZSTD", "zstandard"),
]


@contains("check_cpio")
def check_cpio_deps(self) -> str:
    """Checks that all dependenceis are in the generated CPIO file."""
    for dep in self["dependencies"]:
        _check_in_cpio(self, dep)
    return "All dependencies found in CPIO."


@contains("check_cpio")
def check_cpio_funcs(self) -> str:
    """Checks that all included functions are in the profile included in the generated CPIO file."""
    sh_func_names = [func + "() {" for func in self.included_functions]
    _check_in_cpio(self, "etc/profile", sh_func_names)
    return "All functions found in CPIO."


@contains("check_in_cpio")
@contains("check_cpio")
def check_in_cpio(self) -> str:
    """Checks that all required files and lines are in the generated CPIO file."""
    for file, lines in self["check_in_cpio"].items():
        _check_in_cpio(self, file, lines)
    return "All files and lines found in CPIO."


def _check_in_cpio(self, file, lines=[], quiet=False) -> None:
    """Checks that the file is in the CPIO archive, and it contains the specified lines."""
    cpio = self._cpio_archive
    file = str(file).lstrip("/")  # Normalize as it may be a path
    self.logger.debug("Checking CPIO for dependency: %s" % file)
    if file not in cpio.entries:
        fp = Path(file)
        while str(fp) not in ["/", "."]:
            fp = fp.parent
            if str(fp) not in cpio.entries:
                continue

            if isinstance(cpio.entries[str(fp)], CPIO_Symlink):
                self.logger.debug("Resolving CPIO symlink: %s" % fp)
                return _check_in_cpio(self, cpio.entries[str(fp)].data.decode("ascii").rstrip("\0"), lines, quiet=True)

        if not quiet:
            self.logger.warning("CPIO entries:\n%s" % "\n".join(cpio.entries.keys()))
        raise FileNotFoundError("File not found in CPIO: %s" % file)
    else:
        self.logger.debug("File found in CPIO: %s" % file)

    if lines:
        entry_data = cpio.entries[file].data.decode().splitlines()
        for line in lines:
            if line not in entry_data:
                raise FileNotFoundError("Line not found in CPIO: %s" % line)
            else:
                self.logger.debug("Line found in CPIO: %s" % line)


def _resolve_kernel_config_path(self) -> Path | None:
    """Returns the path to the kernel .config the initramfs will boot under, or None.

    Prefers `kernel_config_file` if `ugrd.kmod.kconfig` already populated it; falls back
    to /lib/modules/<kver>/{build,source}/.config and /boot/config-<kver>.
    """
    if path := self.get("kernel_config_file"):
        return Path(path)

    kver = self.get("kernel_version") or uname().release
    for candidate in (
        Path(f"/lib/modules/{kver}/build/.config"),
        Path(f"/lib/modules/{kver}/source/.config"),
        Path(f"/boot/config-{kver}"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _kernel_supports_decompressor(self, kconfig_suffix: str):
    """Whether the kernel has CONFIG_<kconfig_suffix> set to y or m.

    Returns True/False if the config was inspected, None if the .config could not be located.
    """
    config_path = _resolve_kernel_config_path(self)
    if config_path is None:
        return None

    target = "CONFIG_" + kconfig_suffix + "="
    try:
        with open(config_path) as f:
            for line in f:
                if line.startswith(target):
                    value = line[len(target):].strip()
                    return bool(value) and value[0] in ("y", "m")
    except OSError as e:
        self.logger.warning("Failed to read kernel config %s: %s" % (config_path, e))
        return None
    return False


def _python_compressor_available(module_name: str) -> bool:
    try:
        import_module(module_name)
        return True
    except ImportError:
        return False


def autodetect_compression(self) -> None:
    """Validate or auto-select cpio_compression.

    Behaviour by value:
      - 'auto': pick the best supported backend from _COMPRESSION_CANDIDATES.
      - explicit backend ('xz', 'zstd', or 'true' which means xz): use it if both
        the kernel (CONFIG_RD_<TYPE>) and the Python module are available;
        otherwise fall back to the next supported candidate, finally to no
        compression rather than failing the build.
      - any other string (e.g. 'lz4', 'gzip'): not supported by pycpio, so fall
        back to the best supported candidate or to no compression.
      - 'false' / non-string values: passed through unchanged.

    Kernel config is checked only when it can be located; an unverifiable kernel
    is treated as "trust the user" rather than blocking.
    """
    raw = self.get("cpio_compression")
    if not isinstance(raw, str):
        return
    requested = raw.lower()
    if requested == "false":
        return

    # 'true' is the legacy alias for xz; normalize so we validate it like any other backend.
    if requested == "true":
        requested = "xz"

    auto = requested == "auto"

    if auto:
        candidates = list(_COMPRESSION_CANDIDATES)
    else:
        match = next((c for c in _COMPRESSION_CANDIDATES if c[0] == requested), None)
        if match is None:
            self.logger.warning(
                "[cpio_compression] %s is not a supported backend; falling back to the best available."
                % colorize(raw, "yellow", bold=True)
            )
            # Fall back through the supported backends in priority order (auto-style search).
            candidates = list(_COMPRESSION_CANDIDATES)
            auto = True
        else:
            # Try the requested backend first, then fall back to the others in priority order.
            candidates = [match] + [c for c in _COMPRESSION_CANDIDATES if c[0] != requested]

    chosen = False
    for index, (name, kconfig, module) in enumerate(candidates):
        kernel_ok = _kernel_supports_decompressor(self, kconfig)
        is_requested = not auto and index == 0

        if kernel_ok is False:
            log = self.logger.warning if is_requested else self.logger.info
            log(
                "[cpio_compression] Kernel does not enable %s, skipping %s."
                % (colorize("CONFIG_" + kconfig, "yellow"), colorize(name, "yellow"))
            )
            continue

        if not _python_compressor_available(module):
            self.logger.warning(
                "[cpio_compression] Python module %s is not installed; cannot use %s compression. "
                "Install it (e.g. `pip install %s`) to enable it."
                % (colorize(module, "yellow", bold=True), colorize(name, "yellow"), module)
            )
            continue

        if kernel_ok is None:
            self.logger.warning(
                "[cpio_compression] Kernel config could not be located; "
                "selecting %s without verifying CONFIG_%s."
                % (colorize(name, "cyan"), kconfig)
            )
        elif auto or is_requested:
            self.logger.info("[cpio_compression] Selected %s." % colorize(name, "green", bold=True))
        else:
            self.logger.warning(
                "[cpio_compression] Falling back to %s (requested %s unavailable)."
                % (colorize(name, "green", bold=True), colorize(requested, "yellow"))
            )

        chosen = name
        break

    if chosen is False:
        self.logger.warning(
            "[cpio_compression] No suitable compression backend found; falling back to no compression."
        )

    self["cpio_compression"] = chosen


@unset("out_file")
def get_archive_name(self) -> None:
    """Determines the filename for the output CPIO archive based on the current configuration.
    Sets the 'out_file' key in the configuration dictionary.
    """
    if self.get("kmod_init") and self.get("kernel_version"):
        out_file = f"ugrd-{self['kernel_version']}.cpio"
    else:
        out_file = "ugrd.cpio"

    if compression_type := self["cpio_compression"]:
        # if --compress or --no-compress is set, the type string will be a bool
        if compression_type.lower() != "false":
            # Ignore the extention if compression is set to false
            if compression_type.lower() == "true":
                # If set to true, xz is the default compression type
                compression_type = "xz"
            out_file += f".{compression_type}"
    self["out_file"] = out_file


def make_cpio(self) -> None:
    """
    Populates the CPIO archive using the build directory,
    toggles the deduplication setting based on cpio_deduplicate,
    writes it to the output file, and rotates the output file if necessary.
    Creates device nodes in the CPIO archive if make_nodes is False. (make_nodes will create actual files instead)
    Raises FileNotFoundError if the output directory does not exist.
    """
    cpio = self._cpio_archive
    cpio.deduplicate = self["cpio_deduplicate"]
    cpio.append_recursive(self._get_build_path("/"), relative=True)

    if not self.get("make_nodes"):
        for node in self["nodes"].values():
            self.logger.debug("Adding CPIO node: %s" % node)
            cpio.add_chardev(name=node["path"], mode=node["mode"], major=node["major"], minor=node["minor"])

    out_cpio = self._get_out_path(self["out_file"])
    if not out_cpio.parent.exists():
        self._mkdir(out_cpio.parent, resolve_build=False)

    if out_cpio.exists():
        if self["cpio_rotate"]:
            self._rotate_old(out_cpio)
        elif self["clean"]:
            self.logger.warning("Removing existing file: %s" % colorize(out_cpio, "red", bold=True, bright=True))
            out_cpio.unlink()
        else:
            raise FileExistsError("File already exists, and cleaning/rotation are disabled: %s" % out_cpio)

    cpio.write_cpio_file(out_cpio, compression=self["cpio_compression"], _log_bump=-10)
