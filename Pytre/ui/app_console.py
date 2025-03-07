import sys
import tkinter as tk
from tkinter import ttk, Event
from datetime import datetime

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from about import APP_NAME


class ConsoleWindow(tk.Toplevel):
    def __init__(self, parent=None, hide: bool = False, test: bool = False):
        super().__init__()

        self.parent = parent
        if self.parent is None:
            self.master.withdraw()
        elif hide:
            self.withdraw()
        else:
            self.focus_set()

        self._setup_ui()
        self._events_binds()

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
        sys.stdout = TextRedirector(sys.stdout, self.textbox, "stdout")
        sys.stderr = TextRedirector(sys.stderr, self.textbox, "stderr")

    def reset_stdout(self):
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

    def force_stop(self):
        self.reset_stdout()
        self.destroy()
        if self.parent is None:
            self.quit()

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
    def __init__(self, terminal, textbox: tk.Text, tag: str = "stdout"):
        self.terminal = terminal
        self.textbox: tk.Text = textbox
        self.tag: str = tag
        self.timestamp_tag: str = tag + "_timestamp"

    def write(self, text: str):
        # don't write empty line
        if not text.strip():
            return

        # write to terminal only if it is not None
        if self.terminal:
            self.write_to_terminal(text + "\n")

        self.write_to_textbox(text + "\n")

    def write_to_terminal(self, text: str):
        self.terminal.write(text)
        self.terminal.flush()

    def write_to_textbox(self, text: str):
        timestamp: str = ""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3] + "_" + self.tag + ">"

        padded_text = text[:-1].replace("\n", "\n" + len(timestamp) * " ") + text[-1]

        self.textbox["state"] = "normal"
        self.textbox.insert("end", timestamp, self.timestamp_tag, padded_text, self.tag)
        self.textbox["state"] = "disabled"
        self.textbox.yview(tk.END)

    def flush(self):
        self.terminal.flush()


if __name__ == "__main__":
    my_app = ConsoleWindow(hide=True, test=True)
    my_app.mainloop()
