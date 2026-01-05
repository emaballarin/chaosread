#!/usr/bin/python3
"""Shared utilities for ChaosKey device interaction.

This module provides device detection, USB communication, and test runner
functionality for ChaosKey hardware random number generators.
"""

import shutil
import subprocess
from pathlib import Path
from types import TracebackType

import usb.core
import usb.util


# ChaosKey USB identifiers
CHAOSKEY_VID: int = 0x1D50
CHAOSKEY_PID: int = 0x60C6

# USB endpoints (IN direction, bit 7 set)
ENDPOINT_COOKED: int = 0x85  # Uniformly distributed random bytes (whitened)
ENDPOINT_RAW: int = 0x86  # Raw 12-bit ADC samples (2 bytes per sample)
ENDPOINT_FLASH: int = 0x87  # Firmware image

# USB communication parameters
USB_TIMEOUT_MS: int = 10_000  # 10 second timeout (from C reference)
BULK_TRANSFER_SIZE: int = 1024  # Optimal bulk transfer chunk size


class ChaosKeyDevice:
    """Context manager for ChaosKey USB device access."""

    def __init__(self, serial: str | None = None) -> None:
        """Initialize device handle.

        Args:
            serial: Optional serial number to match specific device.
        """
        self._serial = serial
        self._device: usb.core.Device | None = None
        self._kernel_was_active: bool = False
        self._interface: int = 0

    def __enter__(self) -> "ChaosKeyDevice":
        """Open device and claim interface."""
        # Find device by VID/PID, optionally filtering by serial
        if self._serial:
            # Find all matching devices and filter by serial
            devices = list(
                usb.core.find(
                    find_all=True, idVendor=CHAOSKEY_VID, idProduct=CHAOSKEY_PID
                )
            )
            for dev in devices:
                try:
                    dev_serial = usb.util.get_string(dev, dev.iSerialNumber)
                    if dev_serial == self._serial:
                        self._device = dev
                        break
                except (usb.core.USBError, ValueError):
                    continue
        else:
            self._device = usb.core.find(idVendor=CHAOSKEY_VID, idProduct=CHAOSKEY_PID)

        if self._device is None:
            raise RuntimeError("No ChaosKey device found")

        # Detach kernel driver if active (Linux-specific)
        try:
            if self._device.is_kernel_driver_active(self._interface):
                self._device.detach_kernel_driver(self._interface)
                self._kernel_was_active = True
        except (usb.core.USBError, NotImplementedError):
            pass  # Not supported on all platforms

        # Claim interface
        usb.util.claim_interface(self._device, self._interface)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        """Release interface and reattach kernel driver if needed."""
        if self._device is not None:
            try:
                usb.util.release_interface(self._device, self._interface)
            except usb.core.USBError:
                pass
            if self._kernel_was_active:
                try:
                    self._device.attach_kernel_driver(self._interface)
                except usb.core.USBError:
                    pass
            try:
                usb.util.dispose_resources(self._device)
            except usb.core.USBError:
                pass
        return False

    def read(self, endpoint: int, size: int) -> bytes:
        """Read data from specified endpoint with retry on partial transfers.

        Args:
            endpoint: USB endpoint (ENDPOINT_COOKED, ENDPOINT_RAW, ENDPOINT_FLASH).
            size: Number of bytes to read.

        Returns:
            Bytes read from device.

        Raises:
            RuntimeError: If device is not open.
            usb.core.USBError: On communication failure.
        """
        if self._device is None:
            raise RuntimeError("Device not open")

        buffer = bytearray()
        remaining = size

        while remaining > 0:
            chunk_size = min(remaining, BULK_TRANSFER_SIZE)
            try:
                data = self._device.read(endpoint, chunk_size, timeout=USB_TIMEOUT_MS)
                if len(data) == 0:
                    break  # No more data available
                buffer.extend(data)
                remaining -= len(data)
            except usb.core.USBTimeoutError:
                if buffer:
                    break  # Return what we have
                raise

        return bytes(buffer)

    @property
    def serial(self) -> str | None:
        """Get device serial number."""
        if self._device is None:
            return None
        try:
            return usb.util.get_string(self._device, self._device.iSerialNumber)
        except (usb.core.USBError, ValueError):
            return None


def find_chaoskey_devices() -> list[dict[str, str | int | None]]:
    """Scan for connected ChaosKey devices.

    Returns:
        List of dicts with 'serial', 'bus', 'address' for each device.
    """
    devices: list[dict[str, str | int | None]] = []

    for dev in usb.core.find(
        find_all=True, idVendor=CHAOSKEY_VID, idProduct=CHAOSKEY_PID
    ):
        try:
            serial = usb.util.get_string(dev, dev.iSerialNumber)
        except (usb.core.USBError, ValueError):
            serial = None

        devices.append(
            {
                "bus": dev.bus,
                "address": dev.address,
                "serial": serial,
            }
        )

    return devices


def get_first_chaoskey() -> usb.core.Device | None:
    """Get the first available ChaosKey device.

    Returns:
        USB device object or None if no devices found.
    """
    return usb.core.find(idVendor=CHAOSKEY_VID, idProduct=CHAOSKEY_PID)


def check_test_binaries() -> list[str]:
    """Check for required external test binaries.

    Returns:
        List of missing binary names.
    """
    required = ["ent", "dieharder", "rngtest"]
    return [binary for binary in required if shutil.which(binary) is None]


def run_ent(filename: Path) -> bool:
    """Run the ent entropy analysis tool.

    Args:
        filename: Path to the data file to analyze.

    Returns:
        True if successful, False otherwise.
    """
    output_file = filename.with_suffix(filename.suffix + ".ent.txt")
    print("\n *** Running ent *** \n")

    try:
        with open(output_file, "w") as outf:
            result = subprocess.run(
                ["ent", str(filename)],
                stdout=outf,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return result.returncode == 0
    except OSError as e:
        print(f"Failed to run ent: {e}")
        return False


def run_rngtest(filename: Path) -> bool:
    """Run the rngtest FIPS 140-2 tests.

    Args:
        filename: Path to the data file to analyze.

    Returns:
        True if successful, False otherwise.
    """
    output_file = filename.with_suffix(filename.suffix + ".rngtest.txt")
    print("\n *** Running rngtest *** \n")

    try:
        with open(filename, "rb") as inf, open(output_file, "w") as outf:
            result = subprocess.run(
                ["rngtest"],
                stdin=inf,
                stdout=outf,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return result.returncode == 0
    except OSError as e:
        print(f"Failed to run rngtest: {e}")
        return False


def run_dieharder(filename: Path) -> bool:
    """Run the dieharder statistical test suite.

    Args:
        filename: Path to the data file to analyze.

    Returns:
        True if successful, False otherwise.
    """
    output_file = filename.with_suffix(filename.suffix + ".dieharder.txt")
    print("\n *** Running dieharder *** \n")

    dieharder_args = [
        "dieharder",
        "-a",
        "-g",
        "201",
        "-s",
        "1",
        "-k",
        "2",
        "-Y",
        "1",
        "-f",
        str(filename),
    ]

    try:
        with open(output_file, "w") as outf:
            result = subprocess.run(
                dieharder_args,
                stdout=outf,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return result.returncode == 0
    except OSError as e:
        print(f"Failed to run dieharder: {e}")
        return False
