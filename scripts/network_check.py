"""
Network Check

Verifies dual-network setup:
1. Can reach Tello (192.168.10.1)
2. Can reach internet (for Gemini API)
"""

import socket
import subprocess
import sys


def check_tello_connection() -> bool:
    """Check if Tello is reachable via UDP."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.sendto(b"command", ("192.168.10.1", 8889))
        data, _ = sock.recvfrom(1024)
        sock.close()
        return data == b"ok"
    except Exception:
        return False


def check_internet() -> bool:
    """Check if internet is reachable."""
    try:
        sock = socket.create_connection(("generativelanguage.googleapis.com", 443), timeout=5)
        sock.close()
        return True
    except Exception:
        return False


def main():
    print("TelloAI Network Check")
    print("=" * 40)

    # Check Tello
    print("\n[1/2] Checking Tello connection (192.168.10.1)...")
    tello_ok = check_tello_connection()
    if tello_ok:
        print("  ✓ Tello reachable")
    else:
        print("  ✗ Tello NOT reachable")
        print("    → Make sure WiFi is connected to TELLO-XXXXX network")

    # Check Internet
    print("\n[2/2] Checking internet (Gemini API)...")
    internet_ok = check_internet()
    if internet_ok:
        print("  ✓ Internet reachable")
    else:
        print("  ✗ Internet NOT reachable")
        print("    → Connect USB WiFi adapter or Ethernet for internet")
        print("    → Primary WiFi should be on Tello network")

    # Summary
    print("\n" + "=" * 40)
    if tello_ok and internet_ok:
        print("✓ Dual network setup OK — ready to fly!")
        return 0
    elif tello_ok:
        print("⚠ Tello connected but no internet.")
        print("  You can fly but LLM features won't work.")
        print("  Use --simulate flag for testing without internet.")
        return 1
    elif internet_ok:
        print("⚠ Internet available but Tello not connected.")
        print("  Use --simulate flag for testing without drone.")
        return 1
    else:
        print("✗ Neither Tello nor internet available.")
        print("  Use --simulate flag for offline testing.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
