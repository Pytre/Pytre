import logging
import sys
import tkinter as tk
from tkinter import ttk, Event
from datetime import datetime
from queue import Queue, Empty as QueueIsEmpty
from typing import TextIO

from Pytre_Core.about import APP_NAME
from Pytre_Core.utils import log, LOGGER_ROOT_NAME
from Pytre_Client.app_theme import set_theme, get_system


DATE_FORMAT = "%H:%M:%S"


class ConsoleWindow(tk.Toplevel):
    def __init__(self, parent=None, hide: bool = False, test: bool = False):
        super().__init__()

        self.parent = parent
        if self.parent is None:
            assert isinstance(self.master, tk.Tk)
            self.master.withdraw()

        if hide:
            self.withdraw()
        else:
            self.focus_set()

        set_theme(self)
        self._setup_ui()
        self._events_binds()

        self.console_queue: Queue = Queue()
        self.stop_queue: bool = False
        self.after(100, self._process_console_queue)

        self.redirect_stdio()
        self.log_handler = LogHandler(self)

        if test:
            self.test_output()
            self.test_logger()

    # ------------------------------------------------------------------------------------------
    # Setup UI and binds
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{APP_NAME} - Console output")
        self.geometry("850x550")
        self.resizable(True, True)

        self._setup_filter_bar()
        self._setup_textbox()
        self._setup_textbox_context_menu()
        self._setup_textbox_tags()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._set_position()

    def _set_position(self):
        self.update_idletasks()
        if get_system() == "Windows":
            x = int(self.winfo_screenwidth() / 2 - self.winfo_width() / 2)
            y = int(self.winfo_screenheight() / 2 - self.winfo_height() / 1.8)
            self.geometry(f"+{x}+{y}")

    def _setup_filter_bar(self):
        self.filter_vars: dict[str, tk.BooleanVar] = {}

        bar = ttk.Frame(self)
        bar.grid(column=0, row=0, columnspan=2, sticky="we")

        filter_tags = ("stdout", "stderr", "debug", "info", "warning", "error", "critical")
        for tag in filter_tags:
            var = tk.BooleanVar(value=True)
            self.filter_vars[tag] = var
            cb = ttk.Checkbutton(
                bar, text=tag.upper(), variable=var, command=lambda t=tag, v=var: self._toggle_filter(t, v)
            )
            cb.pack(side="left", padx=4)

    def _setup_textbox(self):
        self.textbox = tk.Text(
            self,
            wrap=tk.CHAR,
            state=tk.DISABLED,
            bg="#141414",
            fg="#F2F2F2",
            selectbackground="#1C518A",
            selectforeground="#F2F2F2",
        )
        x_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.textbox.xview)
        y_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.textbox.yview)

        self.textbox["xscrollcommand"] = x_scrollbar.set
        self.textbox["yscrollcommand"] = y_scrollbar.set

        self.textbox.grid(column=0, row=1, sticky="nswe")
        x_scrollbar.grid(column=0, row=2, sticky="we")
        y_scrollbar.grid(column=1, row=1, sticky="ns")

    def _setup_textbox_context_menu(self):
        """Configure textbox context menu (right click)"""
        self.context_menu = tk.Menu(self.textbox, tearoff=0)
        self.context_menu.add_command(label="Copier (Ctrl+C)", command=self.copy_selection)

        self.textbox.bind("<Button-3>", self.display_context_menu)
        self.textbox.bind("<Control-c>", lambda _: self.copy_selection())
        self.context_menu.bind("<FocusOut>", lambda _: self.context_menu.unpost())

    def _setup_textbox_tags(self):
        self.textbox.tag_configure("stdout", foreground="#FFFFFF")
        self.textbox.tag_configure("stderr", foreground="#FF6A6A")

        self.textbox.tag_configure("debug", foreground="#79CDCD")
        self.textbox.tag_configure("info", foreground="#FFFFFF")
        self.textbox.tag_configure("warning", foreground="#F99924")
        self.textbox.tag_configure("error", foreground="#FF6A6A")
        self.textbox.tag_configure("critical", foreground="#FF4040")

    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.app_exit)

    # ----------------------------------------------
    # UI interaction
    # ----------------------------------------------
    def _toggle_filter(self, tag: str, var: tk.BooleanVar):
        self.textbox.tag_configure(tag, elide=not var.get())

    def display_context_menu(self, event: Event):
        """Display context menu"""
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def copy_selection(self):
        """Copy text to clipboard"""
        try:
            text = self.textbox.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            pass  # no selection

    # ------------------------------------------------------------------------------------------
    # Output channels
    # ------------------------------------------------------------------------------------------
    def redirect_stdio(self):
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr
        sys.stdout = TextRedirector(sys.stdout, self.console_queue, tag="stdout")
        sys.stderr = TextRedirector(sys.stderr, self.console_queue, tag="stderr")

    def reset_stdio(self):
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

    def logger(self, message: str, tag: str = "", end_char: str = "\n"):
        """log message to textbox using main thread"""
        args = (message + end_char, tag)
        self.console_queue.put(args)

    # ------------------------------------------------------------------------------------------
    # Console queue processing
    # ------------------------------------------------------------------------------------------
    def _process_console_queue(self):
        while True:
            try:
                if self.stop_queue:
                    return
                insert_args = self.console_queue.get_nowait()
                self.textbox.config(state=tk.NORMAL)
                self.textbox.insert(tk.END, *insert_args)
                self.textbox.config(state=tk.DISABLED)
                self.textbox.yview(tk.END)
            except QueueIsEmpty:
                self.after(100, self._process_console_queue)
                break
            except Exception as e:
                print(f"Error while processing console queue: {e}")
                self.after(100, self._process_console_queue)
                break

    # ------------------------------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------------------------------
    def force_stop(self):
        self.stop_queue = True

        self.reset_stdio()
        self.destroy()
        if self.parent is None:
            self.quit()

    def app_exit(self, _: Event | None = None):
        if self.parent is None:
            self.force_stop()
        else:
            self.withdraw()

    # ------------------------------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------------------------------
    def test_output(self):
        print(f"test stdout")
        sys.stderr.write(f"et test stderr")
        print("final test\nwith multiple\nlines")

    def test_logger(self):
        log(f"test debug", logging.DEBUG)
        log(f"test info", logging.INFO)
        log(f"test warning", logging.WARNING)
        log(f"test error", logging.ERROR)
        log(f"test critical", logging.CRITICAL)

        log("final test\nwith multiple\nlines", logging.INFO)


class TextRedirector:
    def __init__(self, terminal: TextIO, console_queue: Queue, tag: str = "stdout"):
        self.terminal: TextIO = terminal
        self.console_queue: Queue = console_queue
        self.tag: str = tag

    def write(self, text: str):
        # don't write empty line
        if not text.strip():
            return

        # write to terminal only if it is not None
        if self.terminal:
            self.terminal.write(text + "\n")
            self.terminal.flush()

        # write to textbox
        timestamp: str = datetime.now().strftime(DATE_FORMAT) + f" - {self.tag} - "
        padded_text: str = text.replace("\n", "\n" + len(timestamp) * " ") + "\n"
        insert_args = (timestamp + padded_text, self.tag)
        self.console_queue.put(insert_args)

    def flush(self):
        if self.terminal:
            self.terminal.flush()


class LogHandler(logging.Handler):
    """Handler to redirect to Tkinter console"""

    def __init__(self, app: ConsoleWindow, level: int | str = logging.DEBUG):
        super().__init__()
        self.app: ConsoleWindow = app

        # get logger and add self as a handler
        logger = logging.getLogger(LOGGER_ROOT_NAME)
        logger.addHandler(self)
        logger.setLevel(logging.DEBUG)

        # define handler level and format
        self.setLevel(level)
        self.setFormatter(AlignedFormatter("%(asctime)s - %(levelname)s - %(message)s", DATE_FORMAT))

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        tag = record.levelname.lower()
        self.app.logger(msg, tag=tag)


class AlignedFormatter(logging.Formatter):
    """Formatter that indents continuation lines to align under the message start"""

    def formatMessage(self, record: logging.LogRecord) -> str:
        """Override parent function"""
        message = record.message
        if "\n" not in message:
            return self._style.format(record)

        record.message = ""  # remove message from record to get prefix
        prefix = self._style.format(record)
        indent = " " * len(prefix)
        record.message = message.replace("\n", "\n" + indent)  # change message with indented one

        # get formatted message and restore original message to record
        formatted_msg = self._style.format(record)
        record.message = message

        return formatted_msg


if __name__ == "__main__":
    my_app = ConsoleWindow(hide=False, test=True)
    my_app.mainloop()
