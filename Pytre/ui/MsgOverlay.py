import math
import tkinter as tk
from tkinter import Event
from datetime import datetime, timedelta

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from ui.app_theme import ThemeColors


class MsgOverlay:
    @classmethod
    def display(cls, parent: tk.Tk, message: str = "", wait_ms: int = 0) -> "MsgOverlay":
        """wait_ms : show overlay only after the specified number of ms have elapsed"""
        overlay = MsgOverlay(parent)
        overlay.show(message, wait_ms)
        return overlay

    def __init__(self, parent: tk.Tk):
        self.parent: tk.Tk = parent
        self.can_show: bool = False
        self.spinner_running: bool = False
        self.running_since: datetime = None
        self.show_after_id: str = ""

        self.parent_width: int = 0
        self.parent_height: int = 0

        self._setup_ui()
        self._events_binds()

    def _setup_ui(self):
        # outer overlay to draw a border
        self.overlay = tk.Frame(self.parent, bd=0)
        self.overlay.configure(background=ThemeColors.accent)
        self.overlay.columnconfigure(0, weight=1)

        # inner overlay
        self.inner_overlay = tk.Frame(self.overlay, bd=0, relief="solid")
        self.inner_overlay.configure(bg=ThemeColors.bg_base)
        self.inner_overlay.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.inner_overlay.columnconfigure(0, weight=1)

        # other widgets
        self.label = tk.Label(self.inner_overlay, text="", justify="center")
        self.label.configure(fg=ThemeColors.accent_pressed, bg=ThemeColors.bg_base)

        self.spinner_canvas = tk.Canvas(self.inner_overlay, width=30, height=30, bg="white", highlightthickness=0)

        self.padx: int = 10  # needed for setting width correctly later on
        self.label.grid(row=0, column=0, padx=self.padx, pady=(5, 0))
        self.spinner_canvas.grid(row=1, column=0, padx=self.padx, pady=(0, 5))

        self._setup_spinner()

    def _setup_spinner(self):
        self.num_dots = 7
        self.radius = 9
        self.dot_radius = 3
        self.dots = []
        self.dot_active = 0

        self.dot_color_empty = ThemeColors.accent_light  # "lightgray",
        self.dot_color_filled = ThemeColors.accent  # "black",

        # Création des cercles
        cx, cy = int(self.spinner_canvas.cget("width")) // 2, int(self.spinner_canvas.cget("height")) // 2
        for i in range(self.num_dots):
            angle = 2 * math.pi * i / self.num_dots
            x = cx + self.radius * math.cos(angle)
            y = cy + self.radius * math.sin(angle)
            dot = self.spinner_canvas.create_oval(
                x - self.dot_radius,
                y - self.dot_radius,
                x + self.dot_radius,
                y + self.dot_radius,
                fill=self.dot_color_empty,
                outline="",
            )
            self.dots.append(dot)

    def _spinner_animate(self):
        if not self.spinner_running:
            return

        self.dot_active = (self.dot_active + 1) % self.num_dots

        for i, dot in enumerate(self.dots):
            if i == self.dot_active:
                self.spinner_canvas.itemconfig(self.dots[self.dot_active], fill=self.dot_color_filled)
            else:
                self.spinner_canvas.itemconfig(dot, fill=self.dot_color_empty)

        self.parent.after(150, self._spinner_animate)

    def _setup_size(self, event: Event = None):
        if not self.spinner_running:
            return

        if self.parent_width == self.parent.winfo_width() and self.parent_height == self.parent.winfo_height():
            return
        else:
            self.parent.update_idletasks()
            self.parent_width = self.parent.winfo_width()
            self.parent_height = self.parent.winfo_height()

        # disable wrapping for text_label
        self.label.config(wraplength=0)

        # calculate width to use and apply it to overlay and text label wrap length
        self.overlay.update_idletasks()
        width = min(self.overlay.winfo_reqwidth(), self.parent_width - self.padx * 2)
        self.label.config(wraplength=width - self.padx * 2)
        self.overlay.place_configure(width=width)

        # enforce a minimum ratio between width and height
        self.overlay.update_idletasks()
        min_width = min(self.overlay.winfo_height() * 16 // 6, self.parent_width - self.padx * 2)
        if min_width > self.overlay.winfo_width():
            self.overlay.place_configure(width=min_width)

    def _events_binds(self):
        self.parent.bind("<Configure>", self._setup_size)

    def show(self, message: str = "", wait_ms: int = 0):
        # cancel previous show wait if it exists
        if self.show_after_id:
            self.overlay.after_cancel(self.show_after_id)
            self.show_after_id = ""

        # if already shown, only update message
        if self.overlay.winfo_viewable():
            self.update_msg(message)
            return

        if wait_ms:
            self.show_after_id: str = self.overlay.after(wait_ms, self.show, message)
            return

        self.spinner_running = True
        self.update_msg(message)
        self.overlay.place(relx=0.5, rely=0.5, anchor="center")
        self._setup_size()
        self._spinner_animate()
        self.running_since = datetime.now()

    def update_msg(self, message: str = ""):
        self.label.configure(text=message)

    def hide(self, destroy: bool = True, callback=None) -> bool:
        # ensure overlay is not hiding too quickly
        wait_ms: int = 0
        if self.running_since:
            wait_ms = int((self.running_since + timedelta(milliseconds=750) - datetime.now()).total_seconds() * 1000)
        if wait_ms > 0:
            self.overlay.after(wait_ms, self.hide, destroy, callback)
            return

        # if overlay was waiting to be shown, cancel it's future display
        if self.show_after_id:
            self.overlay.after_cancel(self.show_after_id)
            self.show_after_id = ""

        self.spinner_running = False
        self.running_since = None

        was_shown: bool = False
        if self.overlay.winfo_viewable():
            self.overlay.place_forget()
            was_shown = True

        self.parent.focus_set()

        if destroy:
            self.overlay.destroy()

        if callback:
            callback()

        return was_shown


if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("600x400")

    def start_calc():
        overlay = MsgOverlay.display(root, "Processing...")
        root.after(2000, overlay.hide)

    button = tk.Button(root, text="Launch task", command=start_calc)
    button.grid(row=0, column=0, pady=20)
    root.columnconfigure(0, weight=1)

    root.mainloop()
