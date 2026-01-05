#!/usr/bin/python3
"""Capture large data blocks from ChaosKey and run statistical tests.

This script reads 14GB of data from a ChaosKey device and runs comprehensive
statistical tests:
- ent: Entropy analysis
- rngtest: FIPS 140-2 randomness tests
- dieharder: Comprehensive statistical test suite

Note: Dieharder needs 14GiB of data to not re-use (rewind) input data.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import BinaryIO

import usb.core

from chaoskey_utils import (
    ENDPOINT_COOKED,
    ENDPOINT_RAW,
    ChaosKeyDevice,
    check_test_binaries,
    find_chaoskey_devices,
    run_dieharder,
    run_ent,
    run_rngtest,
)

# Data capture configuration
DEFAULT_NUM_LOOPS: int = 14 * 1024  # 14 GiB for Dieharder to not repeat data
DEFAULT_BLOCK_SIZE: int = 1024 * 1024  # 1 MiB per read

# Endpoint name mapping
ENDPOINT_MAP: dict[str, int] = {
    "cooked": ENDPOINT_COOKED,
    "raw": ENDPOINT_RAW,
}


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="ChaosKey full statistical testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                     # Run with defaults (cooked endpoint, 14 GiB)
  %(prog)s --size 1            # Quick test with 1 GiB
  %(prog)s --endpoint raw      # Test raw ADC samples
  %(prog)s --serial ABC123     # Use specific device by serial
""",
    )
    parser.add_argument(
        "--serial",
        "-s",
        metavar="SERIAL",
        help="Device serial number to use",
    )
    parser.add_argument(
        "--endpoint",
        "-e",
        choices=["cooked", "raw"],
        default="cooked",
        help="Endpoint to read from (default: cooked)",
    )
    parser.add_argument(
        "--size",
        "-n",
        type=int,
        default=14,
        metavar="GiB",
        help="Data size in GiB (default: 14)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        metavar="FILE",
        help="Output filename (default: auto-generated)",
    )
    return parser.parse_args()


def generate_filename(endpoint_name: str = "cooked") -> Path:
    """Generate a timestamped output filename.

    Args:
        endpoint_name: Name of the endpoint being used.

    Returns:
        Path object for the output file.
    """
    datetime_string = time.strftime("%Y%m%d.%H%M%S")
    return Path(f"ChaosKey_{endpoint_name}_{datetime_string}.data")


def capture_data(
    device: ChaosKeyDevice,
    output_file: BinaryIO,
    endpoint: int = ENDPOINT_COOKED,
    num_loops: int = DEFAULT_NUM_LOOPS,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> int:
    """Capture random data from ChaosKey device with progress display.

    Args:
        device: Open ChaosKeyDevice instance.
        output_file: Open file handle to write data to.
        endpoint: USB endpoint to read from.
        num_loops: Number of blocks to read.
        block_size: Size of each block in bytes.

    Returns:
        Total bytes captured.
    """
    total_bytes = 0

    for i in range(num_loops):
        try:
            before = time.time()
            data = device.read(endpoint, block_size)
            after = time.time()
        except usb.core.USBError as e:
            print(f"\nRead failed at block {i + 1}: {e}")
            break

        if len(data) == 0:
            print(f"\nNo data received at block {i + 1}")
            break

        total_bytes += len(data)
        output_file.write(data)

        # Calculate transfer rate
        elapsed = after - before
        if elapsed > 0:
            rate = float(len(data)) / (elapsed * 1_000_000.0) * 8
        else:
            rate = 0.0

        # Display progress
        progress = (i + 1) * 100 / num_loops
        sys.stdout.write(
            f"\r{i + 1} of {num_loops} MiB ({progress:2.1f}%) "
            f"Read at {rate:2.3f} Mbits/s"
        )
        sys.stdout.flush()

    print()  # Newline after progress
    return total_bytes


def main() -> int:
    """Main entry point for full testing.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    # Platform check
    if sys.platform != "linux":
        print("Error: This script only runs on Linux.")
        return 1

    args = parse_args()

    print("ChaosKey Full Testing")
    print("=" * 50)

    # Check for required test binaries first
    missing = check_test_binaries()
    if missing:
        print(f"Missing test binaries: {', '.join(missing)}")
        print("Install with: sudo pacman -S ent rng-tools dieharder")
        print("         or: sudo apt install ent rng-tools dieharder")
        return 1

    # Find ChaosKey devices
    devices = find_chaoskey_devices()

    print("Detected devices:")
    if not devices:
        print("  No ChaosKey devices found!")
        print("\nTroubleshooting:")
        print("  1. Check if device is connected: lsusb | grep 1d50:60c6")
        print("  2. Install udev rules for unprivileged access:")
        print("     sudo cp 99-chaoskey.rules /etc/udev/rules.d/")
        print("     sudo udevadm control --reload-rules")
        print("     sudo udevadm trigger")
        return 1

    for dev in devices:
        print(f"  Bus {dev['bus']:03d} Device {dev['address']:03d}: {dev['serial']}")

    # Determine which device to use
    serial_to_use = args.serial
    if serial_to_use:
        print(f"\nUsing device with serial: {serial_to_use}")
    else:
        print(f"\nUsing first detected device: {devices[0]['serial']}")

    print("=" * 50)

    # Configuration
    endpoint = ENDPOINT_MAP[args.endpoint]
    num_loops = args.size * 1024  # Convert GiB to MiB blocks

    # Generate output filename
    output_filename = args.output or generate_filename(args.endpoint)

    # Print configuration
    print(f"Block Size:      {DEFAULT_BLOCK_SIZE / 1024 / 1024:.2f} MiB")
    print(f"Number of loops: {num_loops}")
    print(f"Total size:      {args.size:.2f} GiB")
    print(f"Endpoint:        {args.endpoint} (0x{endpoint:02X})")
    print(f"Writing to:      {output_filename}")
    print("=" * 50)

    # Capture data
    try:
        with ChaosKeyDevice(serial=serial_to_use) as device:
            print(f"Device opened: {device.serial}")
            print("Starting data capture...")

            with open(output_filename, "wb") as fp:
                total_bytes = capture_data(
                    device, fp, endpoint=endpoint, num_loops=num_loops
                )
                print(f"Captured {total_bytes / 1024 / 1024:.2f} MiB")

    except usb.core.USBError as e:
        if e.errno == 13:  # Permission denied
            print(f"\nPermission denied: {e}")
            print("\nInstall udev rules for unprivileged access:")
            print("  sudo cp 99-chaoskey.rules /etc/udev/rules.d/")
            print("  sudo udevadm control --reload-rules")
            print("  sudo udevadm trigger")
        else:
            print(f"\nUSB error: {e}")
        return 1
    except RuntimeError as e:
        print(f"\nError: {e}")
        return 1
    except OSError as e:
        print(f"\nError opening output file: {e}")
        return 1

    # Run statistical tests
    print("\nRunning statistical tests...")
    run_ent(output_filename)
    run_rngtest(output_filename)
    run_dieharder(output_filename)

    print("\n" + "=" * 50)
    print("Testing complete!")
    print(f"Data file:     {output_filename}")
    print(f"ent results:   {output_filename}.ent.txt")
    print(f"rngtest:       {output_filename}.rngtest.txt")
    print(f"dieharder:     {output_filename}.dieharder.txt")

    return 0


if __name__ == "__main__":
    sys.exit(main())
