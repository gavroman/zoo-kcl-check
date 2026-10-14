"""PyInstaller entrypoint: frozen worker dispatch must precede CLI parsing."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from zoo_kcl_check_cli.cli import main

    raise SystemExit(main())
