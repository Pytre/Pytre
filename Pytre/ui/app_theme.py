from __future__ import annotations

import tkinter as tk
from tkinter import ttk, font

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from utils import get_system


THEME_ON: bool = False


def theme_is_on() -> bool:
    return THEME_ON


def set_theme(root: tk.Toplevel) -> bool:
    global THEME_ON

    if not get_system() == "Windows":
        set_win10_like_theme(root)
        THEME_ON = True

    return THEME_ON


def set_menus(menus: list[tk.Menu], first_is_menubar: bool = True):
    if not menus:
        return

    default_font = font.nametofont("TkDefaultFont")
    font_family = default_font.actual("family")
    font_size = default_font.actual("size")

    # icon in first menu to avoid being garbage collected
    menus[0].empty_icon = tk.PhotoImage(width=16, height=16)

    for i, menu in enumerate(menus):
        menu.config(
            font=font.Font(family=font_family, size=font_size),
            bg=ThemeColors.bg_base if i == 0 and first_is_menubar else ThemeColors.bg_primary,
            fg=ThemeColors.text_primary,
            activebackground=ThemeColors.accent_light if i == 0 and first_is_menubar else ThemeColors.accent,
            activeforeground=ThemeColors.text_primary if i == 0 and first_is_menubar else ThemeColors.text_secondary,
            activeborderwidth=0,
        )

        # display an empty icon for command without icon
        # on linux if there is no icon, no space is reserved for them
        if i == 0 and first_is_menubar:
            continue
        for index in range(menu.index("end") + 1):
            try:
                if not menu.entrycget(index, "image"):
                    menu.entryconfig(index, image=menus[0].empty_icon, compound="left")
            except tk.TclError:
                pass


class _WinColors:
    bg_base = "#ffffff"  # Blanc pour les zones de saisie
    bg_primary = "#F0F0F0"  # Blanc principal "#ffffff"
    bg_secondary = "#f3f3f3"  # Gris très clair
    bg_tertiary = "#dadada"  # Gris clair
    border_primary = "#7A7A7A"  # Bordures
    border_secondary = "#DCDCDC"  # Bordures
    text_primary = "#000000"  # Texte principal
    text_secondary = "#ffffff"  # Texte secondaire
    text_disabled = "#999999"  # Texte désactivé
    scrollbar_normal = "#CDCDCD"  # Scrollbar normal
    scrollbar_hover = "#c0c0c0"  # Scrollbar au survol
    accent = "#0078d4"  # Bleu Windows 10
    accent_hover = "#106ebe"  # Bleu hover
    accent_pressed = "#005a9e"  # Bleu pressed
    accent_light = "#deecf9"  # Bleu très clair
    selection = "#0078d4"  # Sélection
    selection_bg = "#cce4f7"  # Fond sélection
    error = "#d13438"  # Rouge erreur
    success = "#107c10"  # Vert succès


class ThemeColors(_WinColors):
    # standard class to allow easy switch theme colors
    # without modifiyng modules importing it
    pass


def set_win10_like_theme(root: tk.Toplevel, colors=_WinColors):
    theme_name = "win10_like"

    # change default font size
    font_size = 9
    for name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkTooltipFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
    ):
        try:
            font.nametofont(name).configure(size=font_size)
        except tk.TclError:
            pass

    font_normal = ("TkDefaultFont", font_size)
    font_bold = font_normal + ("bold",)

    root.configure(bg=colors.bg_primary)
    theme_settings: dict = {
        # === BOUTONS ===
        "TButton": {
            "configure": {
                "padding": (3, 3),
                "width": 12,
                "relief": "solid",
                "anchor": "center",
                "background": colors.bg_primary,
                "foreground": colors.text_primary,
                "font": font_normal,
                "borderwidth": 1,
                "focuscolor": colors.accent,
                "bordercolor": colors.border_secondary,
            },
            "map": {
                "background": [
                    ("disabled", colors.bg_secondary),
                    ("pressed", colors.accent_pressed),
                    ("active", colors.accent_light),
                ],
                "foreground": [
                    ("disabled", colors.text_disabled),
                    ("pressed", colors.bg_primary),
                ],
                "bordercolor": [
                    ("focus", colors.accent),
                    ("active", colors.accent),
                    ("disabled", colors.border_secondary),
                ],
                "relief": [("pressed", "flat")],
            },
        },
        # === LABELS ===
        "TLabel": {
            "configure": {
                "background": colors.bg_primary,
                "foreground": colors.text_primary,
                "font": font_normal,
            }
        },
        # === FRAMES ===
        "TFrame": {"configure": {"background": colors.bg_primary}},
        "Card.TFrame": {
            "configure": {"background": colors.bg_primary, "borderwidth": 1, "bordercolor": colors.border_secondary}
        },
        # === PANEDWINDOW ===
        "TPanedwindow": {"configure": {"background": colors.bg_primary}},
        "Sash": {
            "configure": {
                "background": colors.bg_primary,
                "lightcolor": colors.bg_primary,
                "bordercolor": colors.bg_primary,
            }
        },
        # === LABELFRAMES ===
        "TLabelframe": {
            "configure": {
                "background": colors.bg_primary,
                "bordercolor": colors.border_secondary,
                "borderwidth": 1,
                "relief": "solid",
            }
        },
        "TLabelframe.Label": {
            "configure": {
                "background": colors.bg_primary,
                "foreground": colors.text_primary,
                "font": font_bold,
                "padding": 4,
            }
        },
        "TEntry": {
            "configure": {
                "fieldbackground": colors.bg_base,
                "background": colors.bg_base,
                "foreground": colors.text_primary,
                "selectbackground": colors.accent,
                "selectforeground": colors.text_secondary,
                "borderwidth": 1,
                "relief": "solid",
                "bordercolor": colors.border_primary,
                "insertcolor": colors.text_primary,
                "font": font_normal,
            },
            "map": {
                "bordercolor": [
                    ("focus", colors.accent),
                    ("invalid", colors.error),
                ],
                "fieldbackground": [
                    ("disabled", colors.bg_secondary),
                ],
                "foreground": [
                    ("disabled", colors.text_disabled),
                ],
            },
        },
        # === COMBOBOX ===
        "TCombobox": {
            "configure": {
                "fieldbackground": colors.bg_base,
                "background": colors.bg_base,
                "foreground": colors.text_primary,
                "selectbackground": colors.accent,
                "selectforeground": colors.text_secondary,
                "borderwidth": 1,
                "relief": "solid",
                "bordercolor": colors.border_primary,
                "arrowcolor": colors.text_primary,
                "font": font_normal,
            },
            "map": {
                "bordercolor": [
                    ("focus", colors.accent),
                    ("active", colors.accent),
                ],
                "fieldbackground": [
                    ("disabled", colors.bg_secondary),
                    ("readonly", colors.bg_base),
                ],
                "foreground": [
                    ("disabled", colors.text_disabled),
                ],
                "arrowcolor": [
                    ("active", colors.accent),
                ],
            },
        },
        # === TREEVIEW ===
        "Treeview": {
            "configure": {
                "background": colors.bg_base,  # fond blanc
                "fieldbackground": colors.bg_base,  # fond des cases
                "foreground": colors.text_primary,
                "rowheight": 22,
                "borderwidth": 1,
                "bordercolor": colors.border_primary,
                "font": font_normal,
            },
            "map": {
                "background": [
                    ("selected", colors.accent),
                    ("!selected", colors.bg_base),
                ],  # bleu clair sélection
                "foreground": [("selected", colors.text_secondary)],
            },
        },
        "Treeview.Heading": {
            "configure": {
                "background": colors.bg_primary,
                "foreground": colors.text_primary,
                "relief": "flat",
                "font": font_normal,
                "padding": 4,
                "borderwidth": 1,
            },
            "map": {"background": [("active", colors.bg_tertiary)]},  # survol en-tête
        },
        # === CHECKBUTTON ===
        "TCheckbutton": {
            "configure": {
                "indicatorcolor": colors.bg_base,
                "background": colors.bg_primary,
                "foreground": colors.text_primary,
                "font": font_normal,
                "focuscolor": colors.accent,
            },
            "map": {
                "indicatorcolor": [
                    ("pressed", colors.accent_light),
                    ("selected", colors.accent),
                ],
            },
        },
        # === RADIOBUTTON ===
        "TRadiobutton": {
            "configure": {
                "indicatorcolor": colors.bg_base,
                "background": colors.bg_primary,
                "foreground": colors.text_primary,
                "font": font_normal,
                "focuscolor": colors.accent,
            },
            "map": {
                "indicatorcolor": [
                    ("pressed", colors.accent_light),
                    ("selected", colors.accent),
                ],
            },
        },
        # === SCALE ===
        "TScale": {
            "configure": {
                "background": colors.bg_secondary,
                "troughcolor": colors.bg_tertiary,
                "borderwidth": 0,
                "lightcolor": colors.accent,
                "darkcolor": colors.accent,
            },
        },
        # === PROGRESSBAR ===
        "TProgressbar": {
            "configure": {
                "background": colors.accent,
                "troughcolor": colors.bg_tertiary,
                "borderwidth": 0,
                "lightcolor": colors.accent,
                "darkcolor": colors.accent,
            },
        },
        # === SCROLLBAR ===
        "TScrollbar": {
            "configure": {
                "background": colors.scrollbar_normal,  # scrollbar
                "troughcolor": colors.bg_primary,  # gouttière
                "bordercolor": colors.bg_primary,
                "arrowsize": 14,
                "width": 16,
                "arrowcolor": colors.text_primary,
                "gripcount": 0,
            },
            "map": {
                "background": [
                    ("!active", colors.scrollbar_normal),  # scrollbar normal
                    ("active", colors.scrollbar_hover),  # scrollbar au survol
                ],
                "lightcolor": [
                    ("!active", colors.scrollbar_normal),  # grip normal
                    ("active", colors.scrollbar_hover),  # grip au survol
                ],
                "darkcolor": [
                    ("!active", colors.scrollbar_normal),  # grip normal
                    ("active", colors.scrollbar_hover),  # grip au survol
                ],
                "arrowcolor": [
                    ("!active", colors.text_primary),
                    ("active", colors.text_primary),
                    ("pressed", colors.text_primary),
                ],
            },
        },
        # === NOTEBOOK ===
        "TNotebook": {
            "configure": {
                "background": colors.bg_primary,
                "borderwidth": 1,
                "bordercolor": colors.border_primary,
            }
        },
        "TNotebook.Tab": {
            "configure": {
                "background": colors.bg_secondary,
                "foreground": colors.text_primary,
                "padding": (12, 4),
                "font": font_normal,
                "borderwidth": 1,
                "bordercolor": colors.border_primary,
            },
            "map": {
                "background": [
                    ("selected", colors.selection_bg),
                    ("active", colors.bg_tertiary),
                ],
                "foreground": [
                    ("selected", colors.text_primary),
                    ("active", colors.text_primary),
                ],
                "bordercolor": [
                    ("selected", colors.accent),
                ],
            },
        },
        # === SPINBOX ===
        "TSpinbox": {
            "configure": {
                "fieldbackground": colors.bg_base,
                "background": colors.bg_base,
                "foreground": colors.text_primary,
                "borderwidth": 1,
                "relief": "solid",
                "bordercolor": colors.border_primary,
                "insertcolor": colors.text_primary,
                "font": font_normal,
                "arrowcolor": colors.text_secondary,
            },
            "map": {
                "bordercolor": [
                    ("focus", colors.accent),
                ],
                "arrowcolor": [
                    ("active", colors.accent),
                ],
            },
        },
        # === SEPARATOR ===
        "TSeparator": {
            "configure": {
                "background": colors.border_primary,
            }
        },
        # === SIZEGRIP ===
        "TSizegrip": {
            "configure": {
                "background": colors.bg_secondary,
            }
        },
    }

    try:
        ttk.Style(root).theme_create(theme_name, parent="clam", settings=theme_settings)
    except tk.TclError:
        pass

    ttk.Style(root).theme_use(theme_name)
