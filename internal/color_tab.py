import ctypes
import json
import re
import time
import tkinter as tk
from colorsys import hsv_to_rgb, rgb_to_hsv
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk
from uuid import uuid4

from PIL import Image, ImageTk

from fish_color_assets import FishColorLibrary, brush_stamp, paint_stroke, recolor, replace_color, sprite_palette
from ocean_ui import ACCENT, EDGE, MUTED, OCEAN as BG, PANEL, TEXT as INK, WATER as SURFACE, rounded_image


FONT = "Malgun Gothic"
CHECKER = "#152F52"
HEX = re.compile(r"#?([a-fA-F0-9]{6})")
DRAWING_TOOLS = {
    "hand": ("이동", "H", "드래그로 화면 이동 · Space"),
    "brush": ("브러시", "B", "드래그해서 칠하기"),
    "bucket": ("페인트통", "G", "이어진 같은 색 채우기"),
    "eyedropper": ("스포이드", "I", "클릭해서 색 가져오기 · Alt+클릭"),
}
DRAWING_SHORTCUTS = {shortcut: tool for tool, (_, shortcut, _) in DRAWING_TOOLS.items()}


def guard_canvas_window_menu(canvas):
    # Alt+Space can reach the native window menu before a Tk binding can stop it.
    wm_syschar, wm_syscommand, sc_keymenu = 0x0106, 0x0112, 0xF100
    alt_context = 1 << 29
    frame = int(str(canvas.winfo_toplevel().wm_frame()), 0)
    controls = ctypes.WinDLL("comctl32")
    message_args = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    subclass = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, *message_args, ctypes.c_size_t, ctypes.c_size_t)
    controls.SetWindowSubclass.argtypes = (wintypes.HWND, subclass, ctypes.c_size_t, ctypes.c_size_t)
    controls.SetWindowSubclass.restype = wintypes.BOOL
    controls.RemoveWindowSubclass.argtypes = (wintypes.HWND, subclass, ctypes.c_size_t)
    controls.RemoveWindowSubclass.restype = wintypes.BOOL
    controls.DefSubclassProc.argtypes = message_args
    controls.DefSubclassProc.restype = ctypes.c_ssize_t

    @subclass
    def dispatch(hwnd, message, wparam, lparam, _id, _data):
        menu = message == wm_syscommand and wparam & 0xFFF0 == sc_keymenu and lparam == ord(" ")
        system_space = message == wm_syschar and wparam == ord(" ") and lparam & alt_context
        if menu or system_space:
            try:
                if canvas.winfo_ismapped() and canvas.focus_get() is canvas:
                    return 0
            except (tk.TclError, KeyError):
                pass
        return controls.DefSubclassProc(hwnd, message, wparam, lparam)

    if not controls.SetWindowSubclass(frame, dispatch, 0, 0):
        return False
    canvas.bind("<Destroy>", lambda _event: controls.RemoveWindowSubclass(frame, dispatch, 0), add="+")
    return True


def drawing_tool_icons(master):
    folder = Path(__file__).with_name("color-tools")
    icons = {}
    for name in DRAWING_TOOLS:
        with Image.open(folder / f"{name}.png") as asset:
            image = asset.convert("RGBA")
        alpha = image.getchannel("A")
        bounds = alpha.point([0] * 128 + [255] * 128).getbbox()
        image = image.crop(bounds)
        image.thumbnail((22, 22), Image.Resampling.NEAREST)
        icon = Image.new("RGBA", (24, 24))
        icon.paste(image, ((24 - image.width) // 2, (24 - image.height) // 2))
        icons[name] = ImageTk.PhotoImage(icon, master=master)
    return icons


@dataclass
class Draft:
    original: Image.Image
    current: Image.Image
    colors: list
    history: list[Image.Image] = field(default_factory=list)
    redo_history: list[Image.Image] = field(default_factory=list)

    def record_edit(self, before):
        self.history.append(before)
        self.redo_history.clear()


class PixelView(tk.Canvas):
    ZOOM_LEVELS = (1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32)

    def __init__(self, parent, on_pick=None, on_drag=None, on_release=None, *, hand_tool=None, on_alt_pick=None):
        super().__init__(parent, bg=BG, highlightthickness=0, width=160, height=160,
                         cursor="crosshair" if on_pick else "arrow")
        self.sprite = None
        self.region = set()
        self.picture = None
        self.origin = (0, 0)
        self.scale = 1
        self.zoom_scale = None
        self.offset = (0, 0)
        self.visible_bounds = None
        self.pan_anchor = None
        self.pointer = None
        self.brush_mask = None
        self.brush_size = 1
        self.brush_outline_point = None
        self.window_menu_guarded = False
        self.hand_tool = hand_tool
        self.alt_down = False
        self.space_down = False
        self.left_down = False
        self.left_panning = False
        self.left_sampling = False
        self.bind("<Configure>", lambda event: self.draw())
        if on_pick is not None:
            self.configure(takefocus=True)
            self.bind("<Enter>", self.enter)
            self.bind("<Motion>", self.track_pointer)
            self.bind("<Leave>", self.clear_pointer)
            self.bind("<Unmap>", self.clear_pointer)
            self.bind("<KeyPress-space>", self.press_space)
            self.bind("<KeyRelease-space>", self.release_space)
            self.bind("<Alt-KeyPress-space>", self.press_space)
            self.bind("<Alt-KeyRelease-space>", self.release_space)
            for key in ("Alt_L", "Alt_R"):
                self.bind(f"<KeyPress-{key}>", self.press_alt)
                self.bind(f"<KeyRelease-{key}>", self.release_alt)
            self.bind("<FocusOut>", self.reset_modifiers)
            if self.tk.call("tk", "windowingsystem") == "win32":
                self.bind("<Map>", self.guard_window_menu)
            self.bind("<MouseWheel>", self.zoom_wheel)
            for button in (2, 3):
                self.bind(f"<ButtonPress-{button}>", self.start_pan)
                self.bind(f"<B{button}-Motion>", self.pan)
                self.bind(f"<ButtonRelease-{button}>", self.finish_pan)
        if on_pick is not None:
            def press(event, *, alternate=False):
                self.focus_set()
                self.left_down = True
                self.left_panning = self.space_down if alternate else self.wants_pan()
                self.left_sampling = alternate and not self.left_panning
                if self.left_panning:
                    self.start_pan(event)
                else:
                    self.pick(event, on_alt_pick if alternate else on_pick)
                self.track_pointer(event)

            self.bind("<Button-1>", press)
            if on_alt_pick is not None:
                self.bind("<Alt-Button-1>", lambda event: press(event, alternate=True))
        if on_drag is not None:
            def drag(event):
                if self.left_panning:
                    self.pan(event)
                else:
                    self.pick(event, on_drag, allow_outside=True)
                self.track_pointer(event)

            self.bind("<B1-Motion>", drag)
        if on_release is not None:
            def release(event):
                if self.left_panning:
                    self.finish_pan(event)
                    self.left_panning = False
                    self.update_cursor()
                elif on_drag is not None:
                    self.pick(event, on_drag, allow_outside=True)
                self.left_down = self.left_sampling = False
                on_release()
                self.track_pointer(event)

            self.bind("<ButtonRelease-1>", release)

    def focus_view(self, _event=None):
        self.focus_set()

    def enter(self, event):
        self.focus_view()
        self.track_pointer(event)

    def guard_window_menu(self, _event):
        if not self.window_menu_guarded:
            self.window_menu_guarded = guard_canvas_window_menu(self)

    def wants_pan(self):
        return self.space_down or (self.hand_tool is not None and self.hand_tool())

    def update_cursor(self):
        self.configure(cursor="hand2" if self.wants_pan() or self.left_panning else "crosshair")
        self.draw_brush_outline()

    def press_space(self, _event):
        # Let an in-progress brush stroke finish with the original tool.
        if not self.left_down:
            self.space_down = True
            self.update_cursor()
        return "break"

    def release_space(self, _event):
        self.space_down = False
        self.update_cursor()
        return "break"

    def press_alt(self, _event):
        self.alt_down = True
        self.draw_brush_outline()

    def release_alt(self, _event):
        self.alt_down = False
        self.draw_brush_outline()

    def reset_modifiers(self, _event):
        self.alt_down = self.space_down = False
        self.update_cursor()

    def track_pointer(self, event):
        self.pointer = (event.x, event.y)
        self.draw_brush_outline()

    def clear_pointer(self, _event):
        self.pointer = None
        self.draw_brush_outline()

    def image_point(self, x, y):
        left, top = self.origin
        return (x - left) // self.scale, (y - top) // self.scale

    def set_brush_preview(self, original, size):
        if original is not self.brush_mask or size != self.brush_size:
            self.brush_mask, self.brush_size = original, size
            self.draw_brush_outline(force=True)

    def draw_brush_outline(self, *, force=False):
        point = None
        sampling = self.left_sampling or (self.alt_down and not self.left_down)
        if (self.pointer is not None and self.brush_mask is not None and self.sprite is not None
                and not (sampling or self.wants_pan() or self.left_panning or self.pan_anchor is not None)):
            x, y = self.pointer
            if 0 <= x < self.winfo_width() and 0 <= y < self.winfo_height():
                px, py = self.image_point(x, y)
                if self.left_down or (0 <= px < self.sprite.width and 0 <= py < self.sprite.height):
                    point = (px, py)
        if point == self.brush_outline_point and not force:
            return
        self.delete("brush-outline")
        self.brush_outline_point = point
        if point is not None:
            region = {(x, y) for x, y, _ in brush_stamp(self.brush_mask, point, self.brush_size)}
            edges = list(self.pixel_edges(region))
            if self.scale >= 4:
                for edge in edges:
                    self.create_line(*edge, fill=BG, width=3, tags="brush-outline")
            for edge in edges:
                self.create_line(*edge, fill=INK, tags="brush-outline")

    def pick(self, event, callback, *, allow_outside=False):
        if self.sprite is None:
            return
        x = min(max(0, event.x), self.winfo_width() - 1) if allow_outside else event.x
        y = min(max(0, event.y), self.winfo_height() - 1) if allow_outside else event.y
        x, y = self.image_point(x, y)
        if allow_outside or (0 <= x < self.sprite.width and 0 <= y < self.sprite.height):
            callback((x, y))

    def centered_origin(self, scale):
        return ((self.winfo_width() - self.sprite.width * scale) // 2,
                (self.winfo_height() - self.sprite.height * scale) // 2)

    def zoom_wheel(self, event):
        if event.delta:
            self.zoom(1 if event.delta > 0 else -1, (event.x, event.y))
        return "break"

    def zoom(self, step, anchor=None):
        if self.sprite is None:
            return
        current = self.ZOOM_LEVELS.index(self.scale)
        target = max(0, min(len(self.ZOOM_LEVELS) - 1, current + step))
        if target == current:
            return
        x, y = anchor or (self.winfo_width() // 2, self.winfo_height() // 2)
        left, top = self.origin
        self.zoom_scale = self.ZOOM_LEVELS[target]
        center_x, center_y = self.centered_origin(self.zoom_scale)
        ratio = self.zoom_scale / self.scale
        self.offset = (round(x - (x - left) * ratio) - center_x,
                       round(y - (y - top) * ratio) - center_y)
        self.draw()

    def fit(self):
        self.zoom_scale = None
        self.offset = (0, 0)
        self.draw()

    def start_pan(self, event):
        self.focus_set()
        self.pan_anchor = (event.x, event.y)
        self.track_pointer(event)

    def pan(self, event):
        if self.sprite is not None and self.pan_anchor is not None:
            x, y = self.pan_anchor
            self.offset = (self.offset[0] + event.x - x, self.offset[1] + event.y - y)
            self.pan_anchor = (event.x, event.y)
            self.pointer = self.pan_anchor
            self.draw()

    def finish_pan(self, event):
        self.pan(event)
        self.pan_anchor = None
        self.track_pointer(event)

    def display(self, sprite, region=()):
        region = set(region)
        if (sprite is not None and self.sprite is not None and sprite.size == self.sprite.size
                and region == self.region and self.picture is not None):
            self.sprite = sprite
            self.picture.paste(self.enlarged_sprite())
            return
        self.sprite = sprite
        self.region = region
        self.draw()

    def enlarged_sprite(self):
        visible = self.sprite.crop(self.visible_bounds)
        return visible.resize((visible.width * self.scale, visible.height * self.scale), Image.Resampling.NEAREST)

    def draw(self):
        self.delete("all")
        self.brush_outline_point = None
        width, height = self.winfo_width(), self.winfo_height()
        if self.sprite is None:
            return
        w, h = self.sprite.size
        scale = self.zoom_scale or max(1, min(8, (width - 28) // w, (height - 28) // h))
        self.scale = scale
        center_x, center_y = self.centered_origin(scale)
        left = center_x if w * scale <= width else min(0, max(width - w * scale, center_x + self.offset[0]))
        top = center_y if h * scale <= height else min(0, max(height - h * scale, center_y + self.offset[1]))
        self.offset = (left - center_x, top - center_y)
        self.origin = (left, top)
        x0, y0 = max(0, -left // scale), max(0, -top // scale)
        x1 = min(w, (width - left + scale - 1) // scale)
        y1 = min(h, (height - top + scale - 1) // scale)
        self.visible_bounds = (x0, y0, x1, y1)
        for y in range(0, height, 16):
            for x in range(0, width, 16):
                if (x // 16 + y // 16) % 2 == 0:
                    self.create_rectangle(x, y, x + 16, y + 16, fill=CHECKER, width=0)
        self.picture = ImageTk.PhotoImage(self.enlarged_sprite())
        self.create_image(left + x0 * scale, top + y0 * scale, anchor="nw", image=self.picture)
        for edge in self.pixel_edges(self.region):
            self.create_line(*edge, fill=ACCENT)
        self.draw_brush_outline()

    def pixel_edges(self, region):
        x0, y0, x1, y1 = self.visible_bounds
        left, top = self.origin
        scale = self.scale
        for x, y in region:
            if not (x0 <= x < x1 and y0 <= y < y1):
                continue
            px, py = left + x * scale, top + y * scale
            for dx, dy, coords in (
                (-1, 0, (px, py, px, py + scale)),
                (1, 0, (px + scale, py, px + scale, py + scale)),
                (0, -1, (px, py, px + scale, py)),
                (0, 1, (px, py + scale, px + scale, py + scale)),
            ):
                if (x + dx, y + dy) not in region:
                    yield coords


class ColorPicker(ttk.Frame):
    WIDTH = 186
    HEIGHT = 130
    BAR_WIDTH = 18

    def __init__(self, parent, on_begin, on_color, on_end):
        super().__init__(parent, style="Color.Panel.TFrame")
        self.hue, self.saturation, self.value = 0.5, 1.0, 1.0
        self.enabled = False
        self.on_begin, self.on_color, self.on_end = on_begin, on_color, on_end
        self.dragging = False
        self.plane = tk.Canvas(self, width=self.WIDTH, height=self.HEIGHT, highlightthickness=0)
        self.plane.pack(side="left", fill="x", expand=True)
        self.bar = tk.Canvas(self, width=self.BAR_WIDTH, height=self.HEIGHT, highlightthickness=0)
        self.bar.pack(side="left", padx=(7, 0))

        bar = Image.new("RGB", (1, self.HEIGHT))
        bar.putdata([self.rgb(y / (self.HEIGHT - 1), 1, 1) for y in range(self.HEIGHT)])
        self.hue_image = ImageTk.PhotoImage(bar.resize((self.BAR_WIDTH, self.HEIGHT)), master=self)
        self.bar.create_image(0, 0, anchor="nw", image=self.hue_image)
        self.value_mask = Image.frombytes(
            "L", (1, self.HEIGHT), bytes(round((1 - y / (self.HEIGHT - 1)) * 255) for y in range(self.HEIGHT)),
        )
        self.plane_width = 0
        self.plane_fill = self.plane.create_image(0, 0, anchor="nw")
        self.plane_ring = [
            self.plane.create_oval(0, 0, 0, 0, outline=color, width=2) for color in (BG, "white")
        ]
        self.bar_ring = [
            self.bar.create_rectangle(0, 0, 0, 0, outline=color, width=2) for color in (BG, "white")
        ]
        for canvas in (self.plane, self.bar):
            canvas.bind("<Button-1>", self.begin)
            canvas.bind("<B1-Motion>", self.move)
            canvas.bind("<ButtonRelease-1>", self.end)
        self.resize_plane(self.WIDTH)
        self.plane.bind("<Configure>", lambda event: self.resize_plane(event.width))
        self.set_enabled(False)

    @staticmethod
    def rgb(hue, saturation, value):
        return tuple(round(channel * 255) for channel in hsv_to_rgb(hue, saturation, value))

    def resize_plane(self, width):
        width = max(2, width)
        if width == self.plane_width:
            return
        self.plane_width = width
        size = width, self.HEIGHT
        self.saturation_mask = Image.frombytes(
            "L", (width, 1), bytes(round(x * 255 / (width - 1)) for x in range(width)),
        ).resize(size)
        self.shade_mask = self.value_mask.resize(size)
        self.white, self.black = Image.new("RGB", size, "white"), Image.new("RGB", size, "black")
        self.plane_image = ImageTk.PhotoImage(self.white, master=self)
        self.plane.itemconfigure(self.plane_fill, image=self.plane_image)
        self.paint_plane()
        self.mark_color()

    def paint_plane(self):
        hue = Image.new("RGB", self.white.size, self.rgb(self.hue, 1, 1))
        shades = Image.composite(hue, self.white, self.saturation_mask)
        self.plane_image.paste(Image.composite(shades, self.black, self.shade_mask))

    def mark_color(self):
        x = self.saturation * (self.plane_width - 1)
        y = (1 - self.value) * (self.HEIGHT - 1)
        for ring, radius in zip(self.plane_ring, (6, 4)):
            self.plane.coords(ring, x - radius, y - radius, x + radius, y + radius)
        y = self.hue * (self.HEIGHT - 1)
        for ring, inset in zip(self.bar_ring, (1, 2)):
            self.bar.coords(ring, inset, y - 5 + inset, self.BAR_WIDTH - 1 - inset, y + 5 - inset)

    def set_enabled(self, enabled):
        self.enabled = enabled
        for canvas, rings in ((self.plane, self.plane_ring), (self.bar, self.bar_ring)):
            canvas.configure(cursor="crosshair" if enabled else "arrow")
            for ring in rings:
                canvas.itemconfigure(ring, state="normal" if enabled else "hidden")

    def set_color(self, rgb):
        hue, saturation, self.value = rgb_to_hsv(*(channel / 255 for channel in rgb))
        self.saturation = saturation
        if saturation and hue != self.hue:
            self.hue = hue
            self.paint_plane()
        self.mark_color()

    def begin(self, event):
        if self.enabled:
            self.dragging = True
            self.on_begin()
            self.move(event)

    def move(self, event):
        if not self.dragging:
            return
        y = max(0, min(1, event.y / (self.HEIGHT - 1)))
        if event.widget is self.bar:
            if y != self.hue:
                self.hue = y
                self.paint_plane()
        else:
            self.saturation = max(0, min(1, event.x / (self.plane_width - 1)))
            self.value = 1 - y
        self.mark_color()
        self.on_color(self.rgb(self.hue, self.saturation, self.value))

    def end(self, _event):
        if self.dragging:
            self.dragging = False
            self.on_end()


class ColorTab(ttk.Frame):
    MIN_WINDOW_HEIGHT = 610

    def __init__(self, parent, game, bridge):
        super().__init__(parent, padding=12)
        self.root = self.winfo_toplevel()
        self.game = game
        self.settings = bridge / "fish-colors.json"
        self.library = None
        self.loading = False
        self.load_results = Queue()
        self.document = {}
        self.saved = {}
        self.settings_error = ""
        self.connection = None
        self.pending = None
        self.preview_window = None
        self.species = None
        self.variants = []
        self.drafts = {}
        self.draft = None
        self.sprite_id = None
        self.seed = None
        self.selected_color = None
        self.color_buttons = []
        self.region = set()
        self.hex_color = tk.StringVar(master=self)
        self.outline_visible = tk.BooleanVar(master=self, value=True)
        self.draw_tool = tk.StringVar(master=self, value="brush")
        self.paint_color = (255, 206, 112)
        self.stroke_before = None
        self.stroke_last = None
        self.syncing_hex = False
        self.picker_edit = None
        self.project_status = tk.StringVar(master=self)
        self.connection_text = tk.StringVar(master=self)
        self.preview_text = tk.StringVar(master=self)
        self.connection_notice = "게임을 실행하면 색을 적용할 수 있어요."
        self.configure_styles()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main = ttk.Frame(self)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(main, style="Color.Panel.TFrame", padding=9)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)
        ttk.Label(sidebar, text="물고기 이름 검색", style="Color.PanelHeading.TLabel").grid(
            row=0, column=0, sticky="w",
        )
        search_panel = ttk.Frame(sidebar, style="Color.Panel.TFrame")
        search_panel.grid(row=1, column=0, sticky="nsew")
        self.query = tk.StringVar(master=self)
        search_entry = ttk.Entry(search_panel, textvariable=self.query, width=17)
        search_entry.pack(fill="x", pady=(8, 6))
        list_frame = ttk.Frame(search_panel, style="Color.Panel.TFrame")
        list_frame.pack(fill="both", expand=True)
        self.fish_list = tk.Listbox(list_frame, width=17, exportselection=False, bg=BG, fg=INK,
                                    selectbackground=SURFACE, selectforeground=ACCENT,
                                    relief="flat", highlightthickness=1, highlightbackground=EDGE,
                                    highlightcolor=ACCENT, font=(FONT, 10), activestyle="none")
        scrollbar = ttk.Scrollbar(list_frame, command=self.fish_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.fish_list.configure(yscrollcommand=scrollbar.set)
        self.fish_list.pack(fill="both", expand=True)
        self.fish_list.bind("<<ListboxSelect>>", self.select_species)
        self.query.trace_add("write", lambda *_: self.fill_fish_list())

        editor = ttk.Frame(main, style="Color.Panel.TFrame", padding=9)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(1, weight=1)
        details = ttk.Frame(editor, style="Color.Panel.TFrame")
        details.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.fish_name = ttk.Label(details, text="물고기를 선택하세요", style="Color.PanelHeading.TLabel")
        self.fish_name.pack(side="left")
        self.variant_box = ttk.Combobox(details, state="disabled", width=11)
        self.variant_box.pack(side="right", padx=(6, 0))
        self.variant_box.bind("<<ComboboxSelected>>", self.select_variant)
        self.grade_box = ttk.Combobox(details, state="disabled", width=7)
        self.grade_box.pack(side="right")
        self.grade_box.bind("<<ComboboxSelected>>", self.select_grade)

        picker_panel = ttk.Frame(editor, style="Color.Panel.TFrame")
        picker_panel.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(12, 0))
        picker_panel.columnconfigure(0, weight=1, minsize=232)
        picker_panel.rowconfigure(1, weight=1)
        self.view = PixelView(editor, self.select_region, self.drag_brush, self.finish_brush,
                              hand_tool=lambda: self.drawing and self.draw_tool.get() == "hand",
                              on_alt_pick=lambda point: self.select_region(point, sample=True))
        self.view.grid(row=1, column=0, sticky="nsew")
        self.view.bind("<KeyPress>", self.draw_tool_shortcut)
        for modifier in ("Alt", "Control"):
            self.view.bind(f"<{modifier}-KeyPress>", lambda _event: None)
        selection = ttk.Frame(picker_panel, style="Color.Panel.TFrame")
        selection.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(selection, text="편집 모드", style="Color.PanelMuted.TLabel").pack(side="left")
        self.selection_mode = ttk.Combobox(
            selection, values=("색 계열 전체", "직접 그리기"), width=12, state="readonly",
        )
        self.selection_mode.current(0)
        self.selection_mode.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.selection_mode.bind("<<ComboboxSelected>>", self.reset_selection)

        mode_tools = ttk.Frame(picker_panel, style="Color.Panel.TFrame")
        mode_tools.grid(row=1, column=0, sticky="nsew")
        mode_tools.columnconfigure(0, weight=1)
        self.family_tools = ttk.Frame(mode_tools, style="Color.Panel.TFrame")
        self.family_tools.grid(row=0, column=0, sticky="ew")
        colors = ttk.Frame(self.family_tools, style="Color.Panel.TFrame")
        colors.pack(fill="x")
        ttk.Label(colors, text="물고기의 대표 색", style="Color.PanelHeading.TLabel").pack(side="left")
        self.outline_toggle = ttk.Checkbutton(
            colors, text="윤곽선 표시", style="Color.Panel.TCheckbutton",
            variable=self.outline_visible, command=self.refresh_view,
        )
        self.outline_toggle.pack(side="right", padx=(8, 0))
        self.color_strip = ttk.Frame(self.family_tools, style="Color.Panel.TFrame")
        self.color_strip.pack(anchor="w", pady=(6, 2))
        ttk.Label(self.family_tools, text="왼쪽 원본 · 오른쪽 현재",
                  style="Color.PanelMuted.TLabel").pack(anchor="w")
        self.drawing_tools = ttk.Frame(mode_tools, style="Color.Panel.TFrame")
        self.drawing_tools.grid(row=0, column=0, sticky="ew")
        self.drawing_tools.columnconfigure(0, weight=1)
        tool_buttons = ttk.Frame(self.drawing_tools, style="Color.Panel.TFrame")
        tool_buttons.grid(row=0, column=0, sticky="ew")
        self.tool_icons = drawing_tool_icons(self)
        for tool, (label, shortcut, _) in DRAWING_TOOLS.items():
            ttk.Radiobutton(tool_buttons, text=f"{label} ({shortcut})", value=tool, variable=self.draw_tool,
                            image=self.tool_icons[tool], compound="image",
                            style="Color.Tool.TRadiobutton", command=self.focus_editor).pack(side="left", padx=(0, 6))
        brush_settings = self.brush_settings = ttk.Frame(self.drawing_tools, style="Color.Panel.TFrame")
        brush_settings.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        brush_settings.columnconfigure(1, weight=1)
        self.brush_size_text = tk.StringVar(master=self, value="1px")
        self.size_down = ttk.Button(brush_settings, text="−", width=2, style="Color.Step.TButton",
                                    command=lambda: self.change_brush_size(-1))
        self.size_down.grid(row=0, column=0)
        self.brush_size = ttk.Scale(brush_settings, from_=1, to=5, orient="horizontal", length=120,
                                    style="Color.Brush.Horizontal.TScale",
                                    command=self.update_brush_size)
        self.brush_size.set(1)
        self.brush_size.grid(row=0, column=1, sticky="ew", padx=5)
        self.brush_size.bind("<ButtonRelease-1>", self.finish_brush_size)
        self.brush_size.bind("<Left>", lambda _event: self.change_brush_size(-1))
        self.brush_size.bind("<Right>", lambda _event: self.change_brush_size(1))
        self.size_up = ttk.Button(brush_settings, text="+", width=2, style="Color.Step.TButton",
                                  command=lambda: self.change_brush_size(1))
        self.size_up.grid(row=0, column=2)
        ttk.Label(brush_settings, textvariable=self.brush_size_text, width=3,
                  style="Color.PanelMuted.TLabel").grid(row=0, column=3, padx=(4, 0))
        self.drawing_tools.grid_remove()
        self.selection_text = tk.StringVar(master=self)
        tk.Frame(mode_tools, bg=EDGE, height=1).grid(row=1, column=0, sticky="ew", pady=(9, 4))
        ttk.Label(mode_tools, textvariable=self.selection_text, wraplength=215,
                  style="Color.PanelMuted.TLabel").grid(row=2, column=0, sticky="w")

        self.palette_panel = ttk.Frame(picker_panel, style="Color.Panel.TFrame")
        self.palette_panel.grid(row=2, column=0, sticky="ew")
        ttk.Label(self.palette_panel, text="적용할 색", style="Color.PanelHeading.TLabel").pack(anchor="w")
        color_row = ttk.Frame(self.palette_panel, style="Color.Panel.TFrame")
        color_row.pack(anchor="w", pady=(6, 5))
        self.color_sample = tk.Frame(color_row, width=27, height=27, bg=SURFACE,
                                     highlightthickness=1, highlightbackground=EDGE)
        self.color_sample.pack(side="left", padx=(0, 6))
        validate_hex = (self.register(lambda value: re.fullmatch(r"#?[a-fA-F0-9]{0,6}", value) is not None), "%P")
        self.hex_entry = ttk.Entry(color_row, textvariable=self.hex_color, width=10,
                                   font=("Consolas", 11), validate="key", validatecommand=validate_hex)
        self.hex_entry.pack(side="left", padx=(0, 8))
        self.hex_entry.bind("<Return>", lambda event: self.sync_hex())
        self.hex_entry.bind("<FocusOut>", lambda event: self.sync_hex())
        self.hex_color.trace_add("write", self.apply_hex_input)
        self.picker = ColorPicker(self.palette_panel, self.begin_picker_edit, self.apply_picker_color, self.end_picker_edit)
        self.picker.pack(fill="x")

        self.preview_button = ttk.Button(editor, text="선택 완료 · 미리보기", command=self.preview,
                                         style="Primary.TButton")
        self.preview_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.set_action_states()
        self.root.bind("<Control-z>", lambda event: self.history_shortcut(self.undo), add="+")
        self.root.bind("<Control-y>", lambda event: self.history_shortcut(self.redo), add="+")
        footer = ttk.Frame(self, padding=(8, 4), style="Color.Panel.TFrame")
        footer.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(footer, textvariable=self.project_status, style="Color.PanelMuted.TLabel").pack(side="left")
        ttk.Label(footer, textvariable=self.connection_text, style="Color.PanelMuted.TLabel").pack(side="right")

    def configure_styles(self):
        style = ttk.Style(self)
        style.configure("Color.Panel.TFrame", background=PANEL)
        style.configure("Color.PanelHeading.TLabel", background=PANEL, font=(FONT, 10, "bold"))
        style.configure("Color.PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=(FONT, 9))
        style.configure("Color.Panel.TCheckbutton", background=PANEL, foreground=INK, font=(FONT, 9),
                        indicatorbackground=BG, indicatorforeground=ACCENT)
        style.map("Color.Panel.TCheckbutton", background=[("active", PANEL)])
        if "Color.tool.face" not in style.element_names():
            normal, hover, selected, focus = (
                rounded_image(self.root, 32, 32, 5, fill, border) for fill, border in
                ((PANEL, None), (SURFACE, None), (SURFACE, EDGE), (SURFACE, ACCENT))
            )
            rail = Image.new("RGB", (6, 22), PANEL)
            rail.paste(EDGE, (0, 10, 6, 12))
            track = ImageTk.PhotoImage(rail, master=self.root)
            thumb = rounded_image(self.root, 8, 22, 4, "#69B9F1")
            disabled_thumb = rounded_image(self.root, 8, 22, 4, "#507193")
            self.root.color_control_images = (normal, hover, selected, focus, track, thumb, disabled_thumb)
            style.element_create("Color.tool.face", "image", normal, ("focus", focus),
                                 ("selected", selected), ("active", hover), border=5, sticky="nsew")
            style.element_create("Color.brush.trough", "image", track, sticky="ew")
            style.element_create("Color.brush.slider", "image", thumb, ("disabled", disabled_thumb))
        style.layout("Color.Tool.TRadiobutton", [
            ("Color.tool.face", {"sticky": "nsew", "children": [
                ("Radiobutton.padding", {"sticky": "nsew", "children": [
                    ("Radiobutton.label", {"sticky": ""}),
                ]}),
            ]}),
        ])
        style.configure("Color.Tool.TRadiobutton", background=PANEL, padding=4)
        style.layout("Color.Brush.Horizontal.TScale", [
            ("Color.brush.trough", {"sticky": "nsew", "children": [
                ("Color.brush.slider", {"side": "left", "sticky": ""}),
            ]}),
        ])
        style.configure("Color.Brush.Horizontal.TScale", background=PANEL)
        style.layout("Color.Step.TButton", [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]}),
        ])
        style.configure("Color.Step.TButton", background=PANEL, foreground=INK, padding=0, font=(FONT, 11))
        style.map("Color.Step.TButton", background=[("active", PANEL)],
                  foreground=[("disabled", MUTED), ("active", ACCENT), ("focus", ACCENT)])

    def change_brush_size(self, step):
        if self.brush_size.instate(["!disabled"]):
            self.brush_size.set(max(1, min(5, round(self.brush_size.get()) + step)))
        return "break"

    def update_brush_size(self, value):
        size = round(float(value))
        self.brush_size_text.set(f"{size}px")
        self.view.set_brush_preview(self.view.brush_mask, size)

    def finish_brush_size(self, _event):
        self.brush_size.set(round(self.brush_size.get()))
        self.view.focus_view()

    def focus_editor(self):
        self.view.focus_view()
        self.refresh_view()

    def draw_tool_shortcut(self, event):
        tool = DRAWING_SHORTCUTS.get(event.keysym.upper())
        if tool is not None and self.drawing and not self.view.left_down:
            self.draw_tool.set(tool)
            self.focus_editor()
            return "break"

    def refresh(self):
        if self.library is not None or self.loading:
            return
        self.loading = True
        self.project_status.set("물고기 불러오는 중…")

        def load():
            try:
                self.load_results.put((FishColorLibrary(self.game), None))
            except Exception as error:
                self.load_results.put((None, error))

        Thread(target=load, daemon=True).start()
        self.after(50, self.finish_loading)

    def finish_loading(self):
        try:
            self.library, error = self.load_results.get_nowait()
        except Empty:
            self.after(50, self.finish_loading)
            return
        self.loading = False
        if error is not None:
            self.project_status.set(f"물고기 불러오기 실패 · {error}")
            return
        try:
            self.document = self.read_settings()
            self.saved = self.document.get("Edits", {})
        except (OSError, ValueError, TypeError) as error:
            self.settings_error = f"저장한 색을 읽을 수 없어요. {error}"
        else:
            try:
                self.migrate_saved_frames()
            except OSError as error:
                self.connection_text.set(f"수조 색상을 저장하지 못했어요. {error}")
        self.fill_fish_list()
        self.fish_list.selection_set(0)
        self.select_species()
        if self.settings_error:
            self.project_status.set(self.settings_error)

    def read_settings(self):
        try:
            data = json.loads(self.settings.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if (not isinstance(data, dict) or data.get("Version") != 1 or
                not isinstance(data.get("Edits"), dict) or
                not all(isinstance(record, dict) for record in data["Edits"].values())):
            raise ValueError("색상 설정 파일을 확인해 주세요.")
        return data

    def atlas_matches(self, region, document=None):
        document = self.document if document is None else document
        return (document.get("Atlas"), document.get("AtlasWidth"), document.get("AtlasHeight")) == region.atlas

    def write_settings(self, document):
        temporary = self.settings.with_name(f"fish-colors-{document['Revision']}.tmp")
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self.settings)
        finally:
            temporary.unlink(missing_ok=True)

    def migrate_saved_frames(self):
        edits = self.saved.copy()
        changed = False
        for sprite_id, record in self.saved.items():
            if not self.library.frames.get(sprite_id) or record.get("Frames"):
                continue
            try:
                original, region = self.library.image(sprite_id)
                if not self.atlas_matches(region):
                    continue
                edited = region.read_patch(record, original)
                edits[sprite_id] = self.library.color_patch(sprite_id, edited)
            except (KeyError, TypeError, ValueError):
                continue
            changed = True
        if changed:
            document = {**self.document, "Revision": uuid4().hex, "Edits": edits}
            self.write_settings(document)
            self.document, self.saved = document, edits

    def fill_fish_list(self):
        if self.library is None:
            return
        term = self.query.get().strip().casefold()
        self.filtered = sorted(
            (key for key in self.library.species
             if term in self.library.name(key).casefold() or term in key.casefold()),
            key=lambda key: self.library.name(key),
        )
        self.fish_list.delete(0, "end")
        for key in self.filtered:
            self.fish_list.insert("end", self.library.name(key))
        if self.species in self.filtered:
            self.fish_list.selection_set(self.filtered.index(self.species))

    def select_species(self, _event=None, *, sprite_id=None):
        selected = self.fish_list.curselection()
        if not selected:
            return
        self.species = self.filtered[selected[0]]
        self.fish_name.configure(text=self.library.name(self.species))
        self.grades = sorted({variant.grade for variant in self.library.species[self.species]})
        self.grade_box.configure(values=[f"{grade}성" if grade else "별 없음" for grade in self.grades],
                                 state="readonly")
        grade = next((v.grade for v in self.library.species[self.species] if v.sprite_id == sprite_id),
                     self.grades[0])
        self.grade_box.current(self.grades.index(grade))
        self.select_grade(sprite_id=sprite_id)

    def select_grade(self, _event=None, *, sprite_id=None):
        grade = self.grades[self.grade_box.current()]
        self.variants = sorted((v for v in self.library.species[self.species] if v.grade == grade),
                               key=lambda variant: variant.color)
        self.variant_box.configure(values=[f"{v.color}번 색상" for v in self.variants], state="readonly")
        index = next((i for i, v in enumerate(self.variants) if v.sprite_id == sprite_id), 0)
        self.variant_box.current(index)
        self.select_variant()

    def select_variant(self, _event=None):
        self.finish_brush()
        variant = self.variants[self.variant_box.current()]
        switched = self.sprite_id != variant.sprite_id
        self.sprite_id = variant.sprite_id
        self.draft = None
        self.seed = None
        self.selected_color = None
        self.region = set()
        try:
            if variant.sprite_id not in self.drafts:
                original, region = self.library.image(variant.sprite_id)
                current = original.copy()
                if (record := self.saved.get(variant.sprite_id)) and self.atlas_matches(region):
                    try:
                        current = region.read_patch(record, original)
                    except (KeyError, TypeError, ValueError):
                        self.project_status.set("저장된 색상을 확인할 수 없어요.")
                self.drafts[variant.sprite_id] = Draft(original, current, sprite_palette(original))
            self.draft = self.drafts[variant.sprite_id]
        except Exception as error:
            messagebox.showerror("물고기를 표시할 수 없어요", str(error), parent=self.root)
        self.show_edit_status()
        self.build_color_buttons()
        self.sync_hex()
        self.refresh_view()
        if switched:
            self.view.fit()

    def build_color_buttons(self):
        for button, _ in self.color_buttons:
            button.destroy()
        self.color_buttons.clear()
        if self.draft is None:
            return
        for index, _ in enumerate(self.draft.colors):
            swatch = tk.PhotoImage(master=self.root, width=22, height=17)
            button = tk.Button(self.color_strip, borderwidth=0, highlightthickness=2, highlightcolor=INK,
                               image=swatch, padx=0, pady=0, cursor="hand2",
                               command=lambda selected=index: self.select_color_group(selected))
            button.grid(row=index // 7, column=index % 7, padx=(0, 3), pady=(0, 2))
            self.color_buttons.append((button, swatch))

    def select_color_group(self, index):
        group = self.draft.colors[index]
        self.selected_color = index
        self.seed = group.anchor
        self.region = group.points
        self.sync_hex()
        self.refresh_view()

    def select_region(self, point, *, sample=False):
        if self.draft is None:
            return
        if self.drawing:
            tool = "eyedropper" if sample else self.draw_tool.get()
            if tool == "bucket":
                before = self.draft.current
                self.draft.current = replace_color(before, self.draft.original, point, self.paint_color)
                if self.draft.current is not before:
                    self.draft.record_edit(before)
                    self.mark_unsaved()
                    self.refresh_view()
            elif tool == "brush":
                self.finish_brush()
                self.stroke_before = self.draft.current
                self.draft.current = self.draft.current.copy()
                self.drag_brush(point)
            elif tool == "eyedropper":
                red, green, blue, alpha = self.draft.current.getpixel(point)
                if alpha:
                    color = (red, green, blue)
                    self.picker.set_color(color)
                    self.apply_picker_color(color)
            return
        for index, group in enumerate(self.draft.colors):
            if point in group.points:
                self.select_color_group(index)
                break

    @property
    def drawing(self):
        return self.selection_mode.current() == 1

    def drag_brush(self, point):
        if self.stroke_before is None:
            return
        previous, self.stroke_last = self.stroke_last, point
        if paint_stroke(
                self.draft.current, self.draft.original, previous or point, point,
                round(self.brush_size.get()), self.paint_color):
            self.view.display(self.draft.current)

    def finish_brush(self):
        if self.stroke_before is None:
            return
        before, self.stroke_before = self.stroke_before, None
        self.stroke_last = None
        if before.tobytes() != self.draft.current.tobytes():
            self.draft.record_edit(before)
            self.mark_unsaved()
        self.set_action_states()

    def reset_selection(self, _event=None):
        self.finish_brush()
        self.selected_color = self.seed = None
        self.region = set()
        if self.drawing:
            self.family_tools.grid_remove()
            self.drawing_tools.grid()
        else:
            self.drawing_tools.grid_remove()
            self.family_tools.grid()
        self.sync_hex()
        self.focus_editor()

    def current_hex(self):
        if self.drawing:
            rgb = self.paint_color
        elif self.seed is not None:
            rgb = self.draft.current.getpixel(self.seed)[:3]
        else:
            return ""
        return "#{:02X}{:02X}{:02X}".format(*rgb)

    def sync_hex(self):
        self.syncing_hex = True
        try:
            self.hex_color.set(self.current_hex())
        finally:
            self.syncing_hex = False

    def apply_hex_input(self, *_):
        if not self.syncing_hex:
            match = HEX.fullmatch(self.hex_color.get())
            if (self.drawing or self.region) and match:
                value = match[1]
                rgb = tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
                if self.drawing:
                    self.paint_color = rgb
                    self.color_sample.configure(bg=self.current_hex())
                    self.picker.set_color(rgb)
                else:
                    self.apply_color(rgb)

    def begin_picker_edit(self):
        self.picker_edit = None if self.drawing else self.draft.current

    def apply_picker_color(self, rgb):
        if self.drawing:
            self.paint_color = rgb
            self.color_sample.configure(bg=self.current_hex())
        else:
            self.apply_color(rgb, from_picker=True)
        self.sync_hex()

    def end_picker_edit(self):
        if self.picker_edit is not None and self.picker_edit.tobytes() != self.draft.current.tobytes():
            self.draft.record_edit(self.picker_edit)
        self.picker_edit = None
        self.set_action_states()

    def apply_color(self, rgb, *, from_picker=False):
        updated = recolor(self.draft.original, self.draft.current, self.region, self.seed, rgb)
        if updated.tobytes() != self.draft.current.tobytes():
            if not from_picker:
                self.draft.record_edit(self.draft.current)
            self.draft.current = updated
            self.mark_unsaved()
        self.refresh_view(sync_picker=not from_picker)

    def mark_unsaved(self):
        self.show_edit_status()

    def show_edit_status(self):
        if self.draft is None:
            return
        _, region = self.library.image(self.sprite_id)
        original = region.encode(self.draft.original)
        stored = self.saved.get(self.sprite_id, {}).get("Pixels") if self.atlas_matches(region) else None
        current = region.encode(self.draft.current)
        if current != (stored or original):
            self.project_status.set("적용하지 않은 변경 사항")
        else:
            self.project_status.set("원본 색상" if current == original else "저장한 색상")

    def history_shortcut(self, action):
        if self.winfo_ismapped() and self.preview_window is None:
            action()
            return "break"

    def undo(self):
        if self.draft is not None:
            self.move_history(self.draft.history, self.draft.redo_history)

    def redo(self):
        if self.draft is not None:
            self.move_history(self.draft.redo_history, self.draft.history)

    def move_history(self, source, destination):
        self.finish_brush()
        if self.picker_edit is not None:
            self.picker.dragging = False
            self.end_picker_edit()
        if source:
            destination.append(self.draft.current)
            self.draft.current = source.pop()
            self.mark_unsaved()
            self.sync_hex()
            self.refresh_view()

    def set_action_states(self):
        image_state = ["!disabled"] if self.draft is not None else ["disabled"]
        self.preview_button.state(image_state)
        color_ready = self.draft is not None and (self.drawing or bool(self.region))
        self.hex_entry.state(["!disabled"] if color_ready else ["disabled"])
        self.picker.set_enabled(color_ready)
        brush_selected = self.drawing and self.draw_tool.get() == "brush"
        if brush_selected:
            self.brush_settings.grid()
        else:
            self.brush_settings.grid_remove()
        brush_ready = self.draft is not None and brush_selected
        original = self.draft.original if brush_ready else None
        self.view.set_brush_preview(original, round(self.brush_size.get()))
        for widget in (self.brush_size, self.size_down, self.size_up):
            widget.state(["!disabled"] if brush_ready else ["disabled"])

    def refresh_view(self, *, sync_picker=True):
        outline = self.region if not self.drawing and self.outline_visible.get() else ()
        self.view.display(self.draft.current if self.draft else None, outline)
        self.view.update_cursor()
        _, shortcut, hint = DRAWING_TOOLS[self.draw_tool.get()]
        self.selection_text.set(f"{shortcut} · {hint}" if self.drawing else "")
        for index, (button, swatch) in enumerate(self.color_buttons):
            anchor = self.draft.colors[index].anchor
            original = "#{:02x}{:02x}{:02x}".format(*self.draft.original.getpixel(anchor)[:3])
            current = "#{:02x}{:02x}{:02x}".format(*self.draft.current.getpixel(anchor)[:3])
            swatch.put(original, to=(0, 0, 11, 17))
            swatch.put(current, to=(11, 0, 22, 17))
            button.configure(bg=PANEL, activebackground=PANEL,
                             highlightbackground=INK if index == self.selected_color else PANEL)
        self.color_sample.configure(bg=self.current_hex() or SURFACE)
        if sync_picker:
            if self.drawing:
                self.picker.set_color(self.paint_color)
            elif self.seed is not None:
                self.picker.set_color(self.draft.current.getpixel(self.seed)[:3])
        self.set_action_states()

    def preview(self):
        if self.draft is None:
            return
        self.finish_brush()
        window = self.preview_window = tk.Toplevel(self.root)
        window.title(f"{self.fish_name.cget('text')} · 색상 미리보기")
        window.configure(bg=BG)
        window.geometry("640x365")
        window.minsize(480, 310)
        window.transient(self.root)
        window.grab_set()
        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        for column, (label, sprite) in enumerate((("원본", self.draft.original), ("편집 결과", self.draft.current))):
            content.columnconfigure(column, weight=1, uniform="preview")
            ttk.Label(content, text=label).grid(row=0, column=column, pady=(0, 6))
            view = PixelView(content)
            view.grid(row=1, column=column, sticky="nsew", padx=5)
            view.display(sprite)
        content.rowconfigure(1, weight=1)
        actions = ttk.Frame(content)
        actions.grid(row=2, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(actions, text="편집으로 돌아가기", command=self.close_preview).pack(side="left", padx=(0, 6))
        self.apply_button = ttk.Button(actions, text="게임에 적용", style="Primary.TButton",
                                       command=lambda: self.submit_color(restore=False))
        self.apply_button.pack(side="left", padx=(0, 6))
        self.restore_button = ttk.Button(actions, text="원본 복원", command=lambda: self.submit_color(restore=True))
        self.restore_button.pack(side="left")
        ttk.Label(content, textvariable=self.preview_text).grid(row=3, column=0, columnspan=2, pady=(6, 0))
        window.protocol("WM_DELETE_WINDOW", self.close_preview)
        window.bind("<Escape>", lambda event: self.close_preview())
        self.update_preview_buttons()

    def close_preview(self):
        if self.preview_window is not None:
            self.preview_window.destroy()
            self.preview_window = None

    def update_preview_buttons(self):
        if self.preview_window is None:
            return
        animation_notice = ""
        ready = self.connection is not None and self.pending is None and not self.settings_error
        if ready and self.library.frames.get(self.sprite_id) and not self.connection.get("AnimationFrames"):
            animation_notice = "수조 애니메이션을 적용하려면 게임을 다시 시작해 주세요."
            ready = False
        feedback = self.connection_text.get()
        self.preview_text.set(self.settings_error or animation_notice or
                              (feedback if self.pending else self.connection_notice or feedback))
        self.apply_button.state(["!disabled"] if ready else ["disabled"])
        self.restore_button.state(["!disabled"] if ready else ["disabled"])

    def update_connection(self, state):
        try:
            same_game = type(state.get("GameRoot")) is str and self.game.samefile(state["GameRoot"])
        except OSError:
            same_game = False
        status = state.get("Colors") if same_game else None
        self.connection = status if isinstance(status, dict) else None
        self.connection_notice = "" if self.connection is not None else (
            "색상 모듈을 연결하려면 게임을 다시 시작해 주세요." if same_game else "게임을 실행하면 색을 적용할 수 있어요."
        )
        if self.pending is not None:
            revision, sprite_id, restore, origin, deadline = self.pending
            if self.connection is not None and status.get("Revision") == revision:
                if status.get("Error"):
                    self.connection_text.set(f"적용 실패 · {status['Error']}")
                    self.pending = None
                elif status.get("Applied"):
                    self.pending = None
                    draft = self.drafts[sprite_id]
                    if restore:
                        draft.current = draft.original.copy()
                        draft.history.clear()
                        draft.redo_history.clear()
                        if sprite_id == self.sprite_id:
                            self.sync_hex()
                            self.refresh_view()
                    if sprite_id == self.sprite_id:
                        self.show_edit_status()
                    self.connection_text.set("게임에서 원본 색을 복원했어요." if restore else "게임에 색을 적용했어요.")
                    if self.preview_window is origin:
                        self.close_preview()
            if self.pending is not None and time.monotonic() >= deadline:
                self.pending = None
                self.connection_text.set("색은 저장했어요. 게임 연결을 확인한 뒤 다시 적용해 주세요.")
        elif self.connection is not None and status.get("Error"):
            self.connection_text.set(f"저장한 색상 적용 실패 · {status['Error']}")
        elif self.connection is not None and self.connection_text.get().startswith("저장한 색상 적용 실패 ·"):
            self.connection_text.set("")
        self.update_preview_buttons()

    def submit_color(self, *, restore):
        if self.connection is None or self.pending is not None or self.settings_error:
            return
        if self.library.frames.get(self.sprite_id) and not self.connection.get("AnimationFrames"):
            return
        try:
            original, region = self.library.image(self.sprite_id)
            # Send original pixels explicitly. The installed texture or the module's prior baseline may be tinted.
            latest = self.read_settings()
            edits = latest.get("Edits", {}).copy() if self.atlas_matches(region, latest) else {}
            edits[self.sprite_id] = self.library.color_patch(self.sprite_id, original if restore else self.draft.current)
            revision = uuid4().hex
            document = {
                "Version": 1, "Revision": revision, "Atlas": region.name,
                "AtlasWidth": region.atlas_width, "AtlasHeight": region.atlas_height, "Edits": edits,
            }
            self.write_settings(document)
        except (OSError, ValueError) as error:
            self.connection_text.set(f"색상을 저장할 수 없어요. {error}")
            self.update_preview_buttons()
            return
        self.document, self.saved = document, edits
        self.pending = (revision, self.sprite_id, restore, self.preview_window, time.monotonic() + 15)
        self.connection_text.set("게임에 적용하는 중…")
        self.update_preview_buttons()
