from collections import defaultdict
from io import BytesIO
from itertools import permutations
import json
import logging
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk
from uuid import uuid4

import UnityPy
from PIL import Image, ImageOps

from ocean_ui import CardGrid, RARITIES
from pc_fish_data import GameData


SORT_FIELDS = ("가나다순", "희귀도순", "별 등급순")
SORT_ORDERS = tuple(permutations(range(len(SORT_FIELDS))))


def catalog_stock(state):
    discovered = {variant for variants in state.get("fishCollection", {}).values()
                  for variant in variants}
    totals = defaultdict(lambda: [0, 0, 0])
    for fish in state["fishList"]:
        variant = fish["Fish"]
        count = totals[variant]
        count[0] += 1
        count[1] += max(0, fish.get("BreedCount", 0))
        count[2] += max(0, fish.get("BreedMaxCount", 0))
    return discovered | totals.keys(), dict(totals)


def catalog_art(install, variants):
    assets = UnityPy.load(str(install / "PCFish_Data/sharedassets0.assets"))
    pictures = {}
    for obj in assets.objects:
        if obj.type.name != "Sprite":
            continue
        variant = obj.peek_name()
        if variant not in variants:
            continue
        fish = obj.read().image.convert("RGBA")
        bounds = fish.getbbox()
        if not bounds:
            continue
        fish = fish.crop(bounds)
        fish.thumbnail((65, 42), Image.Resampling.NEAREST)
        gray = ImageOps.grayscale(fish).convert("RGBA")
        gray.putalpha(fish.getchannel("A"))
        for discovered, sprite in ((True, fish), (False, gray)):
            with BytesIO() as output:
                sprite.save(output, format="PNG")
                pictures[variant, discovered] = output.getvalue()
    return pictures


def read_favorites(path):
    saved = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(saved, list):
        raise ValueError("즐겨찾기 파일 형식이 올바르지 않습니다.")
    return {variant for variant in saved if isinstance(variant, str) and variant}


def write_favorites(path, favorites):
    temporary = path.with_name(f"favorites-{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(sorted(favorites)), encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class CatalogTab(ttk.Frame):
    def __init__(self, parent, game_path, favorites_path):
        super().__init__(parent, padding=16)
        self.game_path = game_path
        self.result = Queue()
        self.install = self.game = self.stock = self.view = None
        self.images = {}
        self.loading = False
        self.rarity = None
        self.favorites_only = tk.BooleanVar(value=False)
        self.favorites_path = favorites_path
        self.favorites_backup = favorites_path.with_suffix(".json.bak")
        self.favorites_status = tk.StringVar(value="")
        self.favorites_pending = {}
        try:
            self.favorites = read_favorites(favorites_path)
        except (OSError, ValueError) as primary_error:
            try:
                self.favorites = read_favorites(self.favorites_backup)
                self.favorites_status.set("즐겨찾기 백업을 불러왔어요.")
            except (OSError, ValueError) as backup_error:
                self.favorites = set()
                if not all(isinstance(error, FileNotFoundError) for error in (primary_error, backup_error)):
                    self.favorites_status.set("저장된 즐겨찾기를 읽지 못했어요.")
        self.visible_variants = []

        filters = ttk.Frame(self)
        filters.pack(fill="x")
        self.filters = {}
        for column, (rarity, label) in enumerate([(None, "전체"), *enumerate(RARITIES[1:], 1)]):
            filters.columnconfigure(column, weight=1, uniform="rarity")
            button = ttk.Button(filters, text=label, width=5,
                                command=lambda selected=rarity: self.select_rarity(selected))
            button.grid(row=0, column=column, sticky="ew", padx=2)
            self.filters[rarity] = button
        self.select_rarity(None)

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(8, 4))
        ttk.Label(controls, text="정렬").pack(side="left", padx=(0, 8))
        self.sort = ttk.Combobox(
            controls, state="readonly", width=29,
            values=[" → ".join(SORT_FIELDS[field] for field in order) for order in SORT_ORDERS])
        self.sort.current(0)
        self.sort.pack(side="left")
        self.sort.bind("<<ComboboxSelected>>", lambda _: self.draw())
        ttk.Checkbutton(controls, text="즐겨찾기만", style="Ocean.TCheckbutton",
                        variable=self.favorites_only, command=self.draw).pack(side="left", padx=12)
        self.summary = tk.StringVar(value="도감 데이터를 읽는 중…")
        ttk.Label(self, textvariable=self.summary).pack(anchor="w", pady=(0, 6))
        self.favorites_notice = ttk.Label(self, textvariable=self.favorites_status)
        self.grid_view = CardGrid(self, card_width=124, toggle_favorite=self.toggle_favorite)
        self.grid_view.pack(fill="both", expand=True)
        self.favorites_status.trace_add("write", self.show_favorites_status)
        self.show_favorites_status()
        self.after(1800, self.refresh_periodically)

    def show_favorites_status(self, *_):
        if self.favorites_status.get():
            self.favorites_notice.pack(before=self.grid_view, anchor="w", pady=(0, 6))
        else:
            self.favorites_notice.pack_forget()

    def toggle_favorite(self, index):
        if not 0 <= index < len(self.visible_variants):
            return
        variant = self.visible_variants[index]
        updated = self.favorites ^ {variant}
        self.favorites_pending[variant] = variant in updated
        try:
            try:
                stored = read_favorites(self.favorites_path)
            except (FileNotFoundError, ValueError):
                try:
                    stored = read_favorites(self.favorites_backup)
                except (FileNotFoundError, ValueError):
                    stored = set()
            persisted = stored | {key for key, marked in self.favorites_pending.items() if marked}
            persisted.difference_update(key for key, marked in self.favorites_pending.items() if not marked)
            write_favorites(self.favorites_path, persisted)
        except OSError as error:
            logging.warning("Could not save favorites: %s", error)
            self.favorites_status.set("즐겨찾기를 저장하지 못했어요. 이번 실행에만 적용돼요.")
        else:
            updated = persisted
            self.favorites_pending.clear()
            self.favorites_status.set("")
            try:
                write_favorites(self.favorites_backup, updated)
            except OSError as error:
                logging.warning("Could not update favorites backup: %s", error)
        self.favorites = updated
        self.draw()

    def select_rarity(self, rarity):
        self.filters[self.rarity].configure(style="TButton")
        self.rarity = rarity
        self.filters[rarity].configure(style="Primary.TButton")
        self.draw()

    def refresh_periodically(self):
        self.refresh()
        self.after(1800, self.refresh_periodically)

    def refresh(self):
        if self.loading or not self.winfo_viewable():
            return
        install = Path(self.game_path.get())
        game = self.game if install == self.install else None
        self.loading = True
        Thread(target=self.read, args=(install, game), daemon=True).start()
        self.after(100, self.poll)

    def read(self, install, game):
        try:
            art = None
            if game is None:
                game = GameData(install=install)
                try:
                    art = catalog_art(install, game.variants)
                except Exception:
                    logging.exception("Could not read catalog artwork")
                    art = {}
            try:
                stock = catalog_stock(game.state())
            except (OSError, ValueError, KeyError, TypeError):
                stock = None
            self.result.put((install, game, art, stock, None))
        except Exception as error:
            self.result.put((install, None, None, None, str(error)))

    def poll(self):
        try:
            install, game, art, stock, error = self.result.get_nowait()
        except Empty:
            self.after(100, self.poll)
            return
        self.loading = False
        if install != Path(self.game_path.get()):
            self.refresh()
            return
        if error is not None:
            self.summary.set(f"도감 데이터를 읽을 수 없습니다 · {error}")
            self.stock = self.view = None
            self.grid_view.set_cards([])
            return
        self.install, self.game = install, game
        if art is not None:
            self.images = {key: tk.PhotoImage(master=self, data=png) for key, png in art.items()}
            self.view = None
        self.stock_available = stock is not None
        self.stock = stock if self.stock_available else (set(), {})
        self.draw()

    def draw(self):
        if self.stock is None:
            return
        discovered, totals = self.stock
        options = self.rarity, self.sort.current(), self.favorites_only.get()
        view = options, self.stock, self.favorites, self.stock_available
        if view == self.view:
            return
        reset = self.view is None or options != self.view[0]
        self.view = view
        order = SORT_ORDERS[options[1]]
        entries = [
            (self.game.names.get(species, species), rarity, stars, variant)
            for variant, (species, rarity, stars) in self.game.variants.items()
            if 1 <= rarity < len(RARITIES) and (self.rarity is None or rarity == self.rarity)
            and (not options[2] or variant in self.favorites)
        ]
        entries.sort(key=lambda entry: tuple(entry[field] for field in order) + (entry[3],))
        cards = []
        for name, rarity, stars, variant in entries:
            found = variant in discovered
            count, remaining, maximum = totals.get(variant, (0, 0, 0))
            detail = f"×{count} · 교배 {remaining}/{maximum}" if count else "보유 없음" if found else "미발견"
            if not self.stock_available:
                detail = "보유 정보 확인 불가"
            cards.append((name, rarity, stars, detail, self.images.get((variant, found)), found))
        found_count = sum(variant in discovered for _, _, _, variant in entries)
        owned_count = sum(totals.get(variant, (0,))[0] for _, _, _, variant in entries)
        self.visible_variants = [variant for _, _, _, variant in entries]
        summary = f"발견한 변종 {found_count}/{len(entries)}종 · 현재 보유 {owned_count}마리"
        if not self.stock_available:
            summary = f"변종 {len(entries)}종 · 게임 저장 데이터를 읽을 수 없어요."
        self.summary.set("표시할 즐겨찾기가 없어요." if options[2] and not entries else summary)
        self.grid_view.set_cards(cards, reset,
                                 favorites=[variant in self.favorites for variant in self.visible_variants])
