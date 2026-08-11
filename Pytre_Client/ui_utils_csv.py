import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from tkinter import messagebox

from Pytre_Client.MsgOverlay import MsgOverlay
from Pytre_Client.ui_utils_thread import tk_call_when_ready


@dataclass(frozen=True)
class CsvTaskConfig:
    """Configuration pour exécuter une opération CSV (import/export) en tâche de fond,
    avec overlay de chargement et verrouillage de l'UI pendant l'exécution.

    La fonction csv_process_fn ne prend aucun argument et doit retourne une valeur booléenne
    La fonction end_fn est toujours appelé
    """

    ui_parent: tk.Toplevel
    ui_lock_fn: Callable[[], None]
    ui_unlock_fn: Callable[[], None]

    overlay_dialog_msg: str
    overlay_dialog_wait_ms: int

    csv_process_fn: Callable[[], bool]

    end_msgbox_title: str
    end_msgbox_msg: str
    end_fn: Callable[[], None] | None = None


def csv_process_async(cfg: CsvTaskConfig) -> None:
    """Traite un fichier CSV de manière asynchrone en affichant une fenetre d'overlay le temps du traitement.
    Une fois le traitement fini et en cas d'erreur end_fn est toujours executé"""
    overlay = MsgOverlay.display(cfg.ui_parent, cfg.overlay_dialog_msg, cfg.overlay_dialog_wait_ms)
    cfg.ui_lock_fn()

    def worker() -> None:
        result: bool = False
        error: Exception | None = None
        try:
            result = cfg.csv_process_fn()
        except Exception as err:
            error = err
        finally:
            # retour dans le thread principal pour mettre à jour l'UI et finaliser
            result_queue.put((result, error))

    def end(result: bool, error: Exception | None) -> None:
        overlay.hide(callback=cfg.ui_unlock_fn)
        if result:
            messagebox.showinfo(title=cfg.end_msgbox_title, message=cfg.end_msgbox_msg, parent=cfg.ui_parent)
        else:
            msg = "Quelque chose ne s'est pas bien passé :\n" + str(error)
            messagebox.showerror(title=cfg.end_msgbox_title, message=msg, parent=cfg.ui_parent)

        if cfg.end_fn:
            cfg.end_fn()

    result_queue = Queue()
    Thread(target=worker, daemon=True).start()
    tk_call_when_ready(cfg.ui_parent, result_queue, end)
