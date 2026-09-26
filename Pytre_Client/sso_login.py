import tkinter as tk
import webbrowser
from collections.abc import Callable
from queue import Queue
from threading import Thread
from tkinter import Event, ttk, messagebox

import Pytre_Client.ui_utils as ui_utils
from Pytre_Client.app_theme import set_theme
from Pytre_Client.ui_utils_thread import tk_call_when_ready
from Pytre_Core.remotes import Remote
from Pytre_Core.sso import LoopbackListener, SsoLoginCancelled, SsoLoginError, login_via_browser


class SsoWaitDialog(tk.Toplevel):
    def __init__(self, remote_label: str, parent: tk.Tk | tk.Toplevel | None = None):
        super().__init__()

        self.parent: tk.Tk | tk.Toplevel | None = parent
        if not self.parent:
            assert isinstance(self.master, tk.Tk)
            self.master.withdraw()
        else:
            self.focus_set()
            ui_utils.ui_disable_parent(self, self.parent)
            self.transient(self.parent)

        self._listener: LoopbackListener | None = None

        set_theme(self)
        self._setup_ui(remote_label)
        self._setup_position(self.parent)
        self._events_binds()

    # ------------------------------------------------------------------------------------------
    # Setting up UI and binds
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self, remote_label: str):
        self.minsize(width=360, height=25)
        self.resizable(False, False)
        self.title("Connexion SSO")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.msg_frame = ttk.Frame(self, padding=16, borderwidth=2)
        self.msg_frame.grid(row=0, column=0, sticky="nswe")

        self._setup_msg_frame(remote_label)

    def _setup_msg_frame(self, remote_label: str):
        msg = f"Connexion à {remote_label}...\nMerci de terminer la connexion dans le navigateur."

        label = ttk.Label(self.msg_frame, text=msg, justify="center", wraplength=340)
        btn_cancel = ttk.Button(self.msg_frame, text="Annuler", command=self.cancel)

        label.grid(row=0, column=0, pady=(0, 20), sticky="ns")
        btn_cancel.grid(row=1, column=0, sticky="ns")

        self.msg_frame.grid_rowconfigure(0, weight=1)
        self.msg_frame.grid_columnconfigure(0, weight=1)

    def _setup_position(self, parent: tk.Tk | tk.Toplevel | None):
        ui_utils.ui_center(self, parent)

    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.close)  # stop app when window closes

    # ------------------------------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------------------------------
    def set_listener(self, listener: LoopbackListener) -> None:
        """Called from the background thread (login_via_browser()'s on_listener_ready hook),
        right before the browser opens - a plain attribute assignment, safe from another thread,
        so it can interrupt a wait that's already blocking."""
        self._listener = listener

    def cancel(self) -> None:
        if self._listener is not None:
            self._listener.shutdown()

    def close(self, _: Event | None = None):
        if self.parent:
            ui_utils.ui_undisable_parent(self, self.parent)
            self.parent.focus_set()
            self.destroy()  # must be after giving back focus
        else:
            self.destroy()
            self.quit()


def prompt_sso_login(
    parent: tk.Tk | tk.Toplevel, remote: Remote, on_done: Callable[[bool], None], save: bool = True
) -> None:
    """Starts a mediated SSO login for `remote` in a background thread (opens the system browser,
    waits for Pytre_Server's loopback redirect), shows a cancellable wait dialog, and calls
    on_done(success) back on the Tk thread once it's over - same contract as prompt_remote_credentials()
    except it can't return synchronously, the login itself being async."""

    dialog = SsoWaitDialog(remote.address.description_for_ui, parent=parent)

    def worker():
        tokens: tuple[str, str]
        try:
            tokens = login_via_browser(
                remote.address.build_url(), webbrowser.open_new_tab, on_listener_ready=dialog.set_listener
            )
            result_queue.put({"status": "ok", "refresh_token": tokens[0], "access_token": tokens[1]})
        except SsoLoginCancelled:
            result_queue.put({"status": "cancelled"})
        except SsoLoginError as e:
            result_queue.put({"status": "error", "msg": str(e)})

    def sso_end(result: dict):
        dialog.close()

        status = result["status"]
        if status == "ok":
            remote.address.refresh_token = result["refresh_token"]
            remote.address.access_token = result["access_token"]
            remote.address.align_username()
            if save:
                remote.save()
            on_done(True)
        elif status == "cancelled":
            on_done(False)
        else:
            messagebox.showerror("Connexion SSO", result["msg"], parent=parent)
            on_done(False)

    result_queue: Queue = Queue()
    Thread(target=worker, daemon=True).start()
    tk_call_when_ready(dialog, result_queue, sso_end)


if __name__ == "__main__":
    from Pytre_Core.remotes import RemoteAddress

    root = tk.Tk()
    # root.withdraw()

    remote_address = RemoteAddress("http://127.0.0.1", 8000, "user_test")
    remote = Remote(None, remote_address)
    prompt_sso_login(root, remote, on_done=lambda ok: print("success:", ok), save=False)
    root.mainloop()
