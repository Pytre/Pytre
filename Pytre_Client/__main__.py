if __name__ == "__main__":
    import argparse
    import sys
    import multiprocessing

    if getattr(sys, "frozen", False):
        multiprocessing.freeze_support()

    multiprocessing.set_start_method("spawn", force=True)

    from Pytre_Core.about import APP_NAME
    from Pytre_Core.context import AppMode
    from Pytre_Client.app import app_start  # import after managing multiprocess for it to work when frozen

    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument(
        "--init-hybrid",
        action="store_true",
        help="create the shared settings database next to the application if it doesn't exist yet",
    )
    args, _unknown = parser.parse_known_args()

    app_start(AppMode.HYBRID if args.init_hybrid else None)  # None: mode detected from the settings database
