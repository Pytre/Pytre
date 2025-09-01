import time
import tkinter as tk
from tkinter import ttk, Event
from pathlib import Path

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

import sql_query
from sql_lexer import SqlLexer, TokenType
from ui.app_theme import set_theme


class DebugWindow(tk.Toplevel):
    def __init__(self, query: sql_query.Query, parent=None):
        super().__init__()

        self.parent = parent
        if self.parent is None:
            self.master.withdraw()
        else:
            self.focus_set()

        self.query: sql_query.Query = query
        self.tabs = {}

        set_theme(self)
        self._setup_ui()
        self._setup_ui_tabs()
        self._events_binds()

        self.update_from_query()

    def _setup_ui(self):
        my_time = time.strftime("%H:%M:%S", time.localtime())

        self.title(f"Debug Window - {self.query.name} - {self.query.description} ({my_time})")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabs_frame = ttk.Notebook(self)
        self.tabs_frame.grid(column=0, row=0, sticky="nswe")
        self.tabs_frame.grid_columnconfigure(0, weight=1)
        self.tabs_frame.grid_rowconfigure(0, weight=1)

    def _setup_ui_tabs(self):
        self._setup_ui_tab("debug", "Debug Cmd")
        self._setup_ui_tab("template", "Template")
        self._setup_ui_tab("params", "Paramètres")

    def _setup_ui_tab(self, tab_id, tab_title: str):
        curr_tab = {}

        curr_tab["frame"] = ttk.Frame(self.tabs_frame)
        self.tabs_frame.add(curr_tab["frame"], text=tab_title)

        curr_tab["textbox"] = tk.Text(curr_tab["frame"], width=150, height=45, wrap="none", state="disabled")
        curr_tab["scrollbar_x"] = ttk.Scrollbar(curr_tab["frame"], orient="horizontal")
        curr_tab["scrollbar_y"] = ttk.Scrollbar(curr_tab["frame"], orient="vertical")

        curr_tab["scrollbar_x"]["command"] = curr_tab["textbox"].xview
        curr_tab["textbox"]["xscrollcommand"] = curr_tab["scrollbar_x"].set

        curr_tab["scrollbar_y"]["command"] = curr_tab["textbox"].yview
        curr_tab["textbox"]["yscrollcommand"] = curr_tab["scrollbar_y"].set

        curr_tab["textbox"].grid(column=0, row=0, sticky="nswe")
        curr_tab["scrollbar_x"].grid(column=0, row=1, sticky="we")
        curr_tab["scrollbar_y"].grid(column=1, row=0, sticky="ns")

        curr_tab["frame"].grid_columnconfigure(0, weight=1)
        curr_tab["frame"].grid_rowconfigure(0, weight=1)

        self.tabs[tab_id] = curr_tab

    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    def app_exit(self, _: Event = None):
        self.destroy()
        if self.parent is None:
            self.quit()

    def update_from_query(self):
        try:
            self.query.update_values()
        except ValueError:
            pass

        params_lst = []
        for param, value in self.query.cmd_params.items():
            val = str(value) if not isinstance(value, str) or param[0:2] == "@!" else "'" + value + "'"
            params_lst.append(f"{param} : {val}")

        self.output_to_textbox(self.tabs["debug"]["textbox"], self.query.get_cmd_for_debug())
        self.output_to_textbox(self.tabs["template"]["textbox"], self.query.get_infos_for_exec()[0])
        self.output_to_textbox(self.tabs["params"]["textbox"], "\n".join(params_lst))

        self.syntax_color(self.tabs["debug"]["textbox"], self.query.get_params_for_debug())
        self.syntax_color(self.tabs["template"]["textbox"], self.query.get_params_for_debug(True))
        self.syntax_color(self.tabs["params"]["textbox"])

    def syntax_color(self, tbox: tk.Text, forced: dict[int, int] = dict()):
        tbox.tag_configure(TokenType.KEYWORD.value, foreground="blue")
        tbox.tag_configure(TokenType.PARAMETER.value, foreground="purple", background="gray90")
        tbox.tag_configure(TokenType.NUMBER.value, foreground="red")
        tbox.tag_configure(TokenType.COMMENT.value, foreground="green")
        tbox.tag_configure(TokenType.TEXT.value, foreground="maroon")

        lexer = SqlLexer(tbox.get("1.0", "end"))
        for _, token in lexer.tokens.items():
            for tag in tbox.tag_names():
                if token.type.value == tag:
                    start_pos = f"1.0 + {token.pos} chars"
                    end_pos = f"1.0 + {token.pos + token.length} chars"
                    tbox.tag_add(tag, start_pos, end_pos)
                if token.pos in forced and not token.type == TokenType.COMMENT:
                    start_pos = f"1.0 + {token.pos} chars"
                    end_pos = f"1.0 + {token.pos + forced[token.pos]} chars"
                    tbox.tag_remove(tag, start_pos, end_pos)
                    tbox.tag_add(TokenType.PARAMETER.value, start_pos, end_pos)

    def output_to_textbox(self, ctrl: tk.Text, text: str = ""):
        ctrl["state"] = "normal"
        ctrl.replace("1.0", "end", text)
        ctrl["state"] = "disabled"


if __name__ == "__main__":
    filenames = (Path.cwd() / "Requetes SQL").glob("*.sql")
    filename = ""
    for filename in filenames:
        break

    my_query = sql_query.Query(filename)
    my_app = DebugWindow(my_query)
    my_app.mainloop()
