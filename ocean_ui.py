import tkinter as tk
from tkinter import ttk

RARITIES = ("기본", "일반", "고급", "희귀", "전설", "신화")

OCEAN = "#91C6BC"
PANEL = "#F6F3C2"
WATER = "#4B9DA9"
ACCENT = "#E37434"
TEXT = "#183E43"
MUTED = "#485D60"
RARITY_COLORS = (MUTED, TEXT, "#235F54", "#235C72", "#89421F", "#6E4775")


def ocean_style(root):
    root.configure(bg=OCEAN)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=OCEAN, foreground=TEXT, font=("Malgun Gothic", 10))
    style.configure("TButton", background=PANEL, bordercolor=WATER, padding=(7, 4))
    for control in ("TEntry", "TSpinbox"):
        style.configure(control, fieldbackground=PANEL, insertcolor=TEXT)
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                    selectbackground=PANEL, selectforeground=TEXT, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
              foreground=[("readonly", TEXT)])
    style.configure("TNotebook", borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL, padding=(18, 8))
    style.map("TNotebook.Tab", background=[("selected", WATER)],
              padding=[("selected", (26, 12))],
              font=[("selected", ("Malgun Gothic", 11, "bold"))])
    style.map("TButton", background=[("active", ACCENT)])
    style.map("TCheckbutton", background=[("active", OCEAN)])
    empty = tk.PhotoImage(master=root, width=16, height=16)
    empty.put(WATER, to=(1, 1, 15, 15))
    empty.put(PANEL, to=(2, 2, 14, 14))
    checked = empty.copy()
    checked.put(ACCENT, to=(2, 2, 14, 14))
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
            canvas.create_rectangle(x, y, x + step - 8, y + 112,
                                    fill=PANEL if owned else "#DCE5DC", outline=WATER if owned else MUTED)
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


