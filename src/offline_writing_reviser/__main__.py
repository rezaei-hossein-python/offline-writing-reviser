from __future__ import annotations

import argparse

from offline_writing_reviser.application import APP_NAME, OfflineWritingReviserApplication, validate_startup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--validate-startup", action="store_true")
    args = parser.parse_args(argv)
    if args.validate_startup:
        return validate_startup()
    return OfflineWritingReviserApplication().run()


if __name__ == "__main__":
    raise SystemExit(main())
