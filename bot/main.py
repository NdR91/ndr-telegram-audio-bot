"""Unified V2 application entry point."""

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    # Keep entrypoint imports lightweight until deployment settings are read.
    from bot.web.main import main as web_main

    web_main()


if __name__ == "__main__":
    main()
