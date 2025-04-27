"""
Entry‑point for the ChirpChirp LoRa data system.

MVP features:
* Simple CLI menu (Tx / Rx / Settings)
* Placeholder pipelines that will later call the real modules
  - transmitter : sensor → encoder → packetizer → sender
  - receiver    : receiver → reassembler → decoder → data_logger
* Robust input‑validation loop so the program never crashes on bad input
* Easy to extend later (e.g. add JSON config loader, GUI, etc.)
"""

import sys
from pathlib import Path

# ---- Placeholder hooks ----------------------------------------------------
# In the MVP we just print a message.  Later these will import and call the
# real pipeline functions (e.g. transmitter.run(), receiver.run(), …).

def run_transmitter() -> None:
    """Start the transmit loop (sensor → encoder → packetizer → sender)."""
    print("\n▶  송신 모드 시작")
    # TODO: import and wire up real transmitter pipeline here
    # from transmitter.main import run as tx_run
    # tx_run()
    print("   (MVP: dummy loop finished)\n")


def run_receiver() -> None:
    """Start the receive loop (receiver → reassembler → decoder → data_logger)."""
    print("\n▶  수신 모드 시작")
    # TODO: import and wire up real receiver pipeline here
    # from receiver.main import run as rx_run
    # rx_run()
    print("   (MVP: dummy loop finished)\n")


def run_settings() -> None:
    """Placeholder for future interactive settings interface."""
    # In the future we will load / update JSON config, device params, etc.
    print("\n⚙️  설정 인터페이스 준비 중...\n")

# ---------------------------------------------------------------------------

MENU_TEXT = """
[LoRaDataSystem 메뉴]
1) 송신 모드 시작
2) 수신 모드 시작
3) 설정 (준비 중)
선택: """


def choose_mode() -> str:
    """Prompt until the user enters 1, 2, or 3; return that choice."""
    while True:
        choice = input(MENU_TEXT).strip()
        if choice in {"1", "2", "3"}:
            return choice
        print("❗ 잘못된 입력입니다. 1, 2, 3 중 하나를 선택하세요.\n")


def main() -> None:
    """CLI dispatcher."""
    choice = choose_mode()

    if choice == "1":
        run_transmitter()
    elif choice == "2":
        run_receiver()
    elif choice == "3":
        run_settings()

    print("프로그램을 종료합니다.")
    # If needed, clean‑up or restart logic could be added here
    sys.exit(0)


if __name__ == "__main__":
    # Ensure we're running from project root so relative imports work later
    # (Optional – can be removed once proper packaging is in place)
    sys.path.append(str(Path(__file__).resolve().parent))
    main()