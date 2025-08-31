if __name__ == "__main__":
    import sys
    import multiprocessing

    if getattr(sys, "frozen", False):
        multiprocessing.freeze_support()

    multiprocessing.set_start_method("spawn", force=True)

    from ui.app import App  # import after managing multiprocess for it to work when frozen

    my_app = App()
    my_app.mainloop()
