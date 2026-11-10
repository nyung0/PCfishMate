import tkinter as tk
from math import isqrt
from tkinter import ttk

RARITIES = ("기본", "일반", "고급", "희귀", "전설", "신화")

OCEAN = "#10274B"
PANEL = "#1A416F"
WATER = "#245889"
EDGE = "#6AACE5"
ACCENT = "#FFCE70"
TEXT = "#F2F7FF"
MUTED = "#B5C8E3"
RARITY_COLORS = (MUTED, TEXT, "#8CE1A8", "#8BD9FF", "#FFD177", "#EDABFF")


def rounded_image(root, width, height, radius, fill, outline=None):
    image = tk.PhotoImage(master=root, width=width, height=height)
    for y in range(height):
        edge = max(0, radius - min(y, height - 1 - y))
        inset = radius - isqrt(radius * radius - edge * edge)
        image.put(outline or fill, to=(inset, y, width - inset, y + 1))
        if outline and 1 <= y < height - 2:
            image.put(fill, to=(inset + 1, y, width - inset - 1, y + 1))
    return image


def rounded_card(canvas, x, y, right, bottom, fill, outline, radius=12):
    points = (x, y + radius, x, y, x + radius, y, right - radius, y,
              right, y, right, y + radius, right, bottom - radius,
              right, bottom, right - radius, bottom, x + radius, bottom,
              x, bottom, x, bottom - radius)
    return canvas.create_polygon(points, smooth=True, fill=fill, outline=outline)


def ocean_style(root):
    if getattr(root, "_ocean_styled", False):
        return
    root._ocean_styled = True
    root.configure(bg=OCEAN)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=OCEAN, foreground=TEXT, font=("Malgun Gothic", 10))
    root.button_images = []
    for name, button_style, colors in (
        ("Ocean", "TButton", ("#285F96", "#367BB8", "#194570")),
        ("Primary", "Primary.TButton", ("#246D57", "#338669", "#174D3D")),
        ("Stop", "Stop.TButton", ("#9B4053", "#B34C63", "#722B3B")),
    ):
        fills = [(color, EDGE) for color in colors]
        fills.extend((("#263D59", "#507193"), (colors[0], ACCENT)))
        normal, hover, pressed, disabled, focused = (
            rounded_image(root, 32, 28, 6, fill, border) for fill, border in fills)
        root.button_images.extend((normal, hover, pressed, disabled, focused))
        element = f"{name}.button"
        style.element_create(element, "image", normal, ("disabled", disabled),
                             ("pressed", pressed), ("active", hover), ("focus", focused),
                             border=8, sticky="nsew")
        style.layout(button_style, [(element, {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"}),
            ]}),
        ]})])
        style.configure(button_style, padding=(13, 7), foreground=TEXT)
        style.map(button_style, foreground=[("disabled", MUTED)])
    for control in ("TEntry", "TSpinbox"):
        style.configure(control, fieldbackground=PANEL, insertcolor=TEXT,
                        bordercolor=EDGE, arrowcolor=TEXT)
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                    selectbackground=PANEL, selectforeground=TEXT, padding=4,
                    bordercolor=EDGE, arrowcolor=TEXT)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
              foreground=[("readonly", TEXT)])
    style.configure("TNotebook", borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL, padding=(18, 8))
    style.map("TNotebook.Tab", background=[("selected", WATER)],
              padding=[("selected", (26, 12))],
              font=[("selected", ("Malgun Gothic", 11, "bold"))])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    rowheight=27, bordercolor=EDGE)
    style.configure("Treeview.Heading", background=WATER, foreground=TEXT, relief="flat")
    style.map("Treeview", background=[("selected", "#367BB8")], foreground=[("selected", TEXT)])
    style.map("Treeview.Heading", background=[("active", "#367BB8")])
    style.configure("TScrollbar", background=WATER, troughcolor=OCEAN, arrowcolor=TEXT)
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    style.map("TCheckbutton", background=[("active", OCEAN)])
    empty = rounded_image(root, 16, 16, 4, PANEL, EDGE)
    checked = rounded_image(root, 16, 16, 4, "#3475AB", EDGE)
    for x, y in ((4, 8), (5, 9), (6, 10), (7, 9), (8, 8), (9, 7), (10, 6), (11, 5)):
        checked.put(TEXT, to=(x, y, x + 1, y + 2))
    root.check_images = (empty, checked)
    style.element_create("Ocean.indicator", "image", empty, ("selected", checked))
    style.layout("Ocean.TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Ocean.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": "w"}),
    ]})])


class CardGrid(ttk.Frame):
    def __init__(self, parent, select_card=None):
        super().__init__(parent)
        self.cards = []
        self.canvas = tk.Canvas(self, background=OCEAN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw())
        self.canvas.bind("<MouseWheel>", self.wheel)
        if select_card:
            self.canvas.bind("<Button-1>", lambda event: self.select(event, select_card))

    def select(self, event, callback):
        width = max(156, self.canvas.winfo_width())
        columns = max(1, width // 156)
        x, y = event.x, self.canvas.canvasy(event.y)
        step = width / columns
        column, row = int(x // step), int(y // 122)
        index = row * columns + column
        if (0 <= column < columns and 0 <= index < len(self.cards)
                and 4 <= x % step <= step - 4 and 4 <= y % 122 <= 116):
            callback(self.cards[index])

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def set_cards(self, cards, reset=True):
        self.cards = cards
        self.draw()
        if reset:
            self.canvas.yview_moveto(0)

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = max(156, canvas.winfo_width())
        columns = max(1, width // 156)
        step = width / columns
        for i, (name, rarity, stars, detail, picture, owned) in enumerate(self.cards):
            row, column = divmod(i, columns)
            x, y = column * step + 4, row * 122 + 4
            center = x + (step - 8) / 2
            rounded_card(canvas, x, y, x + step - 8, y + 112,
                         fill=PANEL if owned else "#172F50", outline=EDGE if owned else "#507193")
            if picture:
                canvas.create_image(center, y + 26, image=picture)
            canvas.create_text(center, y + 60, text=name, width=int(step - 18),
                               fill=TEXT if owned else MUTED, font=("Malgun Gothic", 9, "bold"))
            grade = "★" * stars if stars else "—"
            canvas.create_text(center, y + 83, text=f"{RARITIES[rarity]}  {grade}",
                               fill=RARITY_COLORS[rarity] if owned else MUTED,
                               font=("Malgun Gothic", 9))
            canvas.create_text(center, y + 101, text=detail, fill=TEXT if owned else MUTED,
                               font=("Malgun Gothic", 9))
        rows = (len(self.cards) + columns - 1) // columns
        canvas.configure(scrollregion=(0, 0, width, rows * 122))
