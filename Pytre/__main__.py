if __name__ == "__main__":
    import sys
    import multiprocessing

    if getattr(sys, "frozen", False):
        multiprocessing.freeze_support()

    multiprocessing.set_start_method("spawn", force=True)

    from ui.app import app_start  # import after managing multiprocess for it to work when frozen

    app_start()
