from pathlib import Path

PORT    = 8080
WS_PORT = 8081
APK_DIR = Path(__file__).parent.parent / "apks"
REC_DIR = Path(__file__).parent / "recordings"
TOR_DIR = Path(__file__).parent / "tor"

# ADB ports to auto-connect on startup (covers common emulators)
EMULATOR_PORTS = [
    ("127.0.0.1", 5555),   # BlueStacks 5, Genymotion, AVD default
    ("127.0.0.1", 5565),   # BlueStacks instance 2
    ("127.0.0.1", 5575),   # BlueStacks instance 3
    ("127.0.0.1", 5585),   # BlueStacks instance 4
    ("127.0.0.1", 21503),  # MEmu
    ("127.0.0.1", 62001),  # NOX Player
    ("127.0.0.1", 7555),   # Genymotion alternate
]
