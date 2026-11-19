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
CATALOG_CARD_COLORS = {
    "undiscovered": ("#172F50", "#507193", MUTED),
    "discovered": ("#393449", "#D6A660", "#FFCE70"),
    "owned": ("#15445A", "#67D6B0", "#A2F0D0"),
}


def rounded_image(root, width, height, radius, fill, outline=None, bevel=False):
    image = tk.PhotoImage(master=root, width=width, height=height)
    rgb = tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5))
    for y in range(height):
        edge = max(0, radius - min(y, height - 1 - y))
        inset = radius - isqrt(radius * radius - edge * edge)
        image.put(outline or fill, to=(inset, y, width - inset, y + 1))
        if outline and 1 <= y < height - 1:
            shade = (24 if y == 1 else 13 if y == 2 else -23 if y >= height - 3 else 0) if bevel else 0
            face = "#%02x%02x%02x" % tuple(max(0, min(255, channel + shade)) for channel in rgb)
            image.put(face, to=(inset + 1, y, width - inset - 1, y + 1))
    return image


def tab_image(root, fill, outline, connected=False):
    width, height, radius = 40, 36, 10
    image = tk.PhotoImage(master=root, width=width, height=height)
    for y in range(height):
        edge = max(0, radius - y)
        inset = radius - isqrt(radius * radius - edge * edge)
        image.put(outline, to=(inset, y, width - inset, y + 1))
        if y >= 2:
            image.put(fill, to=(inset + 2, y, width - inset - 2, y + 1))
    if not connected:
        image.put(outline, to=(0, height - 2, width, height))
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
    style.configure("Section.TLabel", font=("Malgun Gothic", 10, "bold"), foreground="#A9D6FF")
    root.button_images = []
    for name, button_style, colors, outline in (
        ("Ocean", "TButton", ("#285F96", "#367BB8", "#194570"), "#163755"),
        ("Primary", "Primary.TButton", ("#16892B", "#1BA637", "#116B23"), "#0A4E1B"),
        ("Stop", "Stop.TButton", ("#DA3036", "#ED3C42", "#AE232A"), "#7B1D24"),
    ):
        fills = [(color, outline) for color in colors]
        fills.extend((("#263D59", "#507193"), (colors[0], ACCENT)))
        normal, hover, pressed, disabled, focused = (
            rounded_image(root, 32, 28, 5, fill, border, bevel=True) for fill, border in fills)
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
        style.configure(button_style, padding=(13, 5), foreground=TEXT)
        style.map(button_style, background=[("active", OCEAN), ("pressed", OCEAN)])
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
    tab_normal = tab_image(root, PANEL, "#416D99")
    tab_selected = tab_image(root, OCEAN, "#9BC9EC", connected=True)
    tab_hover = tab_image(root, "#2E6596", "#86B9E1")
    root.button_images.extend((tab_normal, tab_selected, tab_hover))
    style.element_create("Ocean.tab", "image", tab_normal, ("selected", tab_selected),
                         ("active", tab_hover), border=12, padding=0, sticky="nsew")
    style.layout("TNotebook.Tab", [("Ocean.tab", {"sticky": "nsew", "children": [
        ("Notebook.padding", {"sticky": "nsew", "children": [
            ("Notebook.label", {"sticky": ""}),
        ]}),
    ]})])
    client = tk.PhotoImage(master=root, width=12, height=12)
    client.put("#9BC9EC", to=(0, 0, 12, 12))
    client.put(OCEAN, to=(2, 2, 10, 10))
    root.button_images.append(client)
    style.element_create("Ocean.client", "image", client, border=2, padding=2, sticky="nsew")
    style.layout("TNotebook", [("Ocean.client", {"sticky": "nsew"})])
    style.configure("TNotebook", tabmargins=(0, 0, 0, -2))
    style.configure("TNotebook.Tab", padding=(18, 7), font=("Malgun Gothic", 10, "bold"))
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
    root.check_images = []
    for icon in (empty, checked):
        indicator = tk.PhotoImage(master=root, width=icon.width() + 5, height=icon.height())
        indicator.tk.call(indicator, "copy", icon)
        root.check_images.append(indicator)
    style.element_create("Ocean.indicator", "image", root.check_images[0],
                         ("selected", root.check_images[1]))
    style.layout("Ocean.TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Ocean.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": "w"}),
    ]})])


def game_notebook_frame(root, source, notebook):
    style = ttk.Style(root)
    width, height = source.width(), source.height()
    frame = tk.PhotoImage(master=root, width=width, height=height)
    frame.tk.call(frame, "copy", source)
    frame.put(OCEAN, to=(8, 8, width - 8, height - 8))
    tabs = []
    for fill, connected in ((PANEL, False), (OCEAN, True), (WATER, False)):
        tab_height = 32
        tab = tk.PhotoImage(master=root, width=width, height=tab_height)
        tab.tk.call(tab, "copy", source, "-from", 0, 0, width, 16, "-to", 0, 0)
        tab.tk.call(tab, "copy", source, "-from", 0, height - 16, width, height, "-to", 0, 16)
        tab.put(fill, to=(4, 4, width - 4, tab_height - 4))
        if connected:
            tab.put(fill, to=(4, tab_height - 8, width - 4, tab_height))
            for y in range(tab_height - 8, tab_height):
                tab.tk.call(tab, "copy", source, "-from", 0, 24, 4, 25, "-to", 0, y)
                tab.tk.call(tab, "copy", source, "-from", width - 4, 24, width, 25,
                            "-to", width - 4, y)
        tabs.append(tab)
    root.button_images.extend((frame, *tabs))
    normal, selected, hover = tabs
    style.element_create("GameFrame.client", "image", frame, border=8, padding=8, sticky="nsew")
    style.element_create("GameFrame.tab", "image", normal, ("selected", selected),
                         ("active", hover), border=8, padding=0, sticky="nsew")
    style.layout("TNotebook", [("GameFrame.client", {"sticky": "nsew"})])
    style.layout("TNotebook.Tab", [("GameFrame.tab", {"sticky": "nsew", "children": [
        ("Notebook.padding", {"sticky": "nsew", "children": [
            ("Notebook.label", {"sticky": ""}),
        ]}),
    ]})])
    style.configure("TNotebook", tabmargins=(0, 0, 0, -2))
    style.configure("TNotebook.Tab", anchor="center", padding=(18, 7), expand=(0, 0, 0, 0))
    style.map("TNotebook.Tab",
              padding=[("selected", (18, 7)), ("!selected", (18, 7))],
              expand=[("selected", (0, 0, 0, 0)), ("!selected", (0, 0, 0, 0))],
              font=[("selected", ("Malgun Gothic", 10, "bold")),
                    ("!selected", ("Malgun Gothic", 10, "bold"))])
    seam = tk.Frame(notebook, bg=OCEAN, borderwidth=0, highlightthickness=0)

    def connect_selected_tab(_event=None):
        selected_tab = notebook.select()
        if not selected_tab:
            seam.place_forget()
            return
        index = notebook.index(selected_tab)
        # ttk does not expose tab bounds through bbox on Windows.
        positions = [(x, notebook.identify(x, 10)) for x in range(notebook.winfo_width())]
        pixels = [x for x, element in positions
                  if element and notebook.index(f"@{x},10") == index]
        if not pixels:
            seam.place_forget()
            return
        x = pixels[0]
        right = pixels[-1] + 1
        y = 10
        while y < notebook.winfo_height():
            try:
                if notebook.index(f"@{(x + right) // 2},{y}") != index:
                    break
            except tk.TclError:
                break
            y += 1
        seam.place(x=x + 3, y=y - 5, width=max(1, right - x - 6), height=14)
        seam.lift()

    notebook.bind("<<NotebookTabChanged>>", connect_selected_tab, add="+")
    notebook.bind("<Configure>", connect_selected_tab, add="+")
    notebook.after_idle(connect_selected_tab)



class CardGrid(ttk.Frame):
    def __init__(self, parent, select_card=None, card_width=156, toggle_favorite=None):
        super().__init__(parent)
        self.cards = []
        self.card_width = card_width
        self.toggle_favorite = toggle_favorite
        self.favorites = ()
        self.canvas = tk.Canvas(self, background=OCEAN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self.resize)
        self.canvas.bind("<MouseWheel>", self.wheel)
        if select_card:
            self.canvas.bind("<Button-1>", lambda event: self.select(event, select_card))
        if toggle_favorite:
            self.canvas.bind("<Button-1>", self.click_favorite, add="+")

    def resize(self, event):
        if event.width != getattr(self, "_drawn_width", None):
            self.draw()

    def click_favorite(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for item in self.canvas.find_overlapping(x, y, x, y):
            for tag in self.canvas.gettags(item):
                if tag.startswith("favorite-"):
                    self.toggle_favorite(int(tag.removeprefix("favorite-")))
                    return

    def select(self, event, callback):
        width = max(self.card_width, self.canvas.winfo_width())
        columns = max(1, width // self.card_width)
        x, y = event.x, self.canvas.canvasy(event.y)
        step = width / columns
        column, row = int(x // step), int(y // 122)
        index = row * columns + column
        if (0 <= column < columns and 0 <= index < len(self.cards)
                and 4 <= x % step <= step - 4 and 4 <= y % 122 <= 116):
            callback(self.cards[index])

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def set_cards(self, cards, reset=True, favorites=()):
        self.cards = cards
        self.favorites = favorites
        self.draw()
        if reset:
            self.canvas.yview_moveto(0)

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        self._drawn_width = canvas.winfo_width()
        width = max(self.card_width, self._drawn_width)
        columns = max(1, width // self.card_width)
        step = width / columns
        for i, (name, rarity, stars, detail, picture, state) in enumerate(self.cards):
            row, column = divmod(i, columns)
            x, y = column * step + 4, row * 122 + 4
            center = x + (step - 8) / 2
            visible = bool(state) and state != "undiscovered"
            fill, outline, detail_color = CATALOG_CARD_COLORS.get(
                state, (PANEL, EDGE, TEXT) if visible else ("#172F50", "#507193", MUTED))
            rounded_card(canvas, x, y, x + step - 8, y + 112,
                         fill=fill, outline=outline)
            if picture:
                canvas.create_image(center, y + 26, image=picture)
            if self.toggle_favorite is not None and self.favorites[i] is not None:
                favorite = self.favorites[i]
                right = x + step - 8
                tag = f"favorite-{i}"
                canvas.create_rectangle(right - 30, y + 4, right - 4, y + 30,
                                        fill=WATER if favorite else OCEAN, width=0, tags=tag)
                canvas.create_text(right - 17, y + 17, text="★" if favorite else "☆",
                                   fill=ACCENT if favorite else MUTED,
                                   font=("Segoe UI Symbol", 12), tags=tag)
            canvas.create_text(center, y + 60, text=name, width=int(step - 18),
                               fill=TEXT if visible else MUTED, font=("Malgun Gothic", 9, "bold"))
            grade = RARITIES[rarity]
            if stars is not None:
                grade += "  " + ("★" * stars or "—")
            canvas.create_text(center, y + 83, text=grade,
                               fill=RARITY_COLORS[rarity] if visible else MUTED,
                               font=("Malgun Gothic", 9))
            canvas.create_text(center, y + 101, text=detail, fill=detail_color,
                               font=("Malgun Gothic", 9))
        rows = (len(self.cards) + columns - 1) // columns
        canvas.configure(scrollregion=(0, 0, width, rows * 122))
