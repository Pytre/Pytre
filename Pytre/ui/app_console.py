import sys
import tkinter as tk
from tkinter import ttk, Event
from datetime import datetime
from queue import Queue, Empty as QueueIsEmpty

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from about import APP_NAME
from ui.app_theme import set_theme, get_system


class ConsoleWindow(tk.Toplevel):
    def __init__(self, parent=None, hide: bool = False, test: bool = False):
        super().__init__()

        self.parent = parent
        if self.parent is None:
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

        self.redirect_stdout()
        if test:
            self.test_output()

    def _setup_ui(self):
        self.title(f"{APP_NAME} - Console output")
        self.geometry("850x550")
        self.resizable(True, True)

        self._setup_textbox()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._set_position()

    def _set_position(self):
        self.update_idletasks()
        if get_system() == "Windows":
            x = int(self.winfo_screenwidth() / 2 - self.winfo_width() / 2)
            y = int(self.winfo_screenheight() / 2 - self.winfo_height() / 1.8)
            self.geometry(f"+{x}+{y}")

    def _setup_textbox(self):
        self.textbox = tk.Text(self, wrap=tk.CHAR, state=tk.DISABLED, bg="#141414", fg="#F2F2F2")
        x_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.textbox.xview)
        y_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.textbox.yview)

        self.textbox["xscrollcommand"] = x_scrollbar.set
        self.textbox["yscrollcommand"] = y_scrollbar.set

        self.textbox.grid(column=0, row=0, sticky="nswe")
        x_scrollbar.grid(column=0, row=1, sticky="we")
        y_scrollbar.grid(column=1, row=0, sticky="ns")

        self._setup_tags()

    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.app_exit)

    def _setup_tags(self):
        self.textbox.tag_configure("stdout_timestamp", foreground="#79CDCD")
        self.textbox.tag_configure("stderr_timestamp", foreground="#FF6A6A")

    def redirect_stdout(self):
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr
        sys.stdout = TextRedirector(sys.stdout, self.console_queue, "stdout")
        sys.stderr = TextRedirector(sys.stderr, self.console_queue, "stderr")

    def reset_stdout(self):
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

    def force_stop(self):
        self.stop_queue = True

        self.reset_stdout()
        self.destroy()
        if self.parent is None:
            self.quit()

    def _process_console_queue(self):
        while True:
            try:
                if self.stop_queue:
                    return
                insert_args = self.console_queue.get_nowait()
                self.textbox["state"] = "normal"
                self.textbox.insert("end", *insert_args)
                self.textbox["state"] = "disabled"
                self.textbox.yview(tk.END)
            except QueueIsEmpty:
                self.after(100, self._process_console_queue)
                break
            except Exception as e:
                print(f"Error while processing console queue: {e}")

    def app_exit(self, _: Event = None):
        if self.parent is None:
            self.force_stop()
        else:
            self.withdraw()

    def test_output(self):
        for i in range(10):
            print(f"test stdout {i}")
            sys.stderr.write(f"et test stderr {i}")
            print("final test\nwith multiple\nlines")


class TextRedirector:
    def __init__(self, terminal, console_queue: Queue, tag: str = "stdout"):
        self.terminal = terminal
        self.console_queue: Queue = console_queue
        self.tag: str = tag
        self.timestamp_tag: str = tag + "_timestamp"

    def write(self, text: str):
        # don't write empty line
        if not text.strip():
            return

        # write to terminal only if it is not None
        if self.terminal:
            self.terminal.write(text + "\n")
            self.terminal.flush()

        # write to textbox
        timestamp: str = datetime.now().strftime("%H:%M:%S") + ">"
        padded_text: str = text.replace("\n", "\n" + len(timestamp) * " ") + "\n"
        insert_args = (timestamp, self.timestamp_tag, padded_text, self.tag)
        self.console_queue.put(insert_args)

    def flush(self):
        if self.terminal:
            self.terminal.flush()


if __name__ == "__main__":
    my_app = ConsoleWindow(hide=False, test=True)
    my_app.mainloop()
