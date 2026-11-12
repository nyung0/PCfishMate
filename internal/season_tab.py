from collections import Counter
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk

import UnityPy
from PIL import Image, ImageOps

from ocean_ui import CardGrid
from pc_fish_data import GameData


def load_grade_icon(install):
    assets = UnityPy.load(str(install / "PCFish_Data/sharedassets0.assets"))
    for obj in assets.objects:
        if obj.type.name == "Sprite" and obj.peek_name() == "Icon_Grade":
            with BytesIO() as buffer:
                obj.read().image.save(buffer, format="PNG")
                return buffer.getvalue()
    return None


def load_automation_icons(install):
    sizes = {
        "Icon_BreedCountOn": 20, "Icon_BreedCountOff": 20,
        "Icon_BreedHeart": 16, "Icon_LvUp": 16, "Icon_Merge": 16,
    }
    icons = {}
    assets = UnityPy.load(str(install / "PCFish_Data/sharedassets0.assets"))
    for obj in assets.objects:
        if obj.type.name != "Sprite":
            continue
        name = obj.peek_name()
        if name not in sizes:
            continue
        icon = obj.read().image.convert("RGBA")
        size = sizes[name]
        if size == 16 and (bounds := icon.getbbox()):
            icon = icon.crop(bounds)
        icon.thumbnail((size, size), Image.Resampling.NEAREST)
        with BytesIO() as buffer:
            icon.save(buffer, format="PNG")
            icons[name] = buffer.getvalue()
        if len(icons) == len(sizes):
            break
    return icons


def load_install_fish(install):
    assets = UnityPy.load(str(install / "PCFish_Data/sharedassets0.assets"))
    for obj in assets.objects:
        if obj.type.name == "Sprite" and obj.peek_name() == "FS00025_04_01_01":
            # The original Coral Clownfish sprite faces left.
            fish = obj.read().image.convert("RGBA")
            bounds = fish.getbbox()
            if bounds:
                fish = ImageOps.mirror(fish.crop(bounds))
                fish.thumbnail((32, 24), Image.Resampling.NEAREST)
                return fish
            break
    return None


def load_season_art(game, install):
    materials = {key for recipe in game.recipes.values() for key in recipe}
    variants = {}
    for variant, (species, rarity, stars) in sorted(game.variants.items()):
        key = (game.names.get(species, species), stars)
        if key in materials and (key not in variants or rarity > variants[key][0]):
            variants[key] = rarity, variant

    wanted = {variant for _, variant in variants.values()}
    images = {}
    assets = UnityPy.load(str(install / "PCFish_Data/sharedassets0.assets"))
    for obj in assets.objects:
        if obj.type.name != "Sprite":
            continue
        name = obj.peek_name()
        if name not in wanted:
            continue
        fish = obj.read().image.convert("RGBA")
        fish = fish.crop(fish.getbbox())
        fish.thumbnail((62, 42), Image.Resampling.NEAREST)
        gray = ImageOps.grayscale(fish).convert("RGBA")
        gray.putalpha(fish.getchannel("A"))
        for owned, sprite in ((True, fish), (False, gray)):
            with BytesIO() as buffer:
                sprite.save(buffer, format="PNG")
                images[name, owned] = buffer.getvalue()
    return variants, images


class SeasonTab(ttk.Frame):
    def __init__(self, parent, game_path):
        super().__init__(parent, padding=16)
        self.game_path = game_path
        self.result = Queue()
        self.loading = False
        self.install = None
        self.game = None
        self.recipes = []
        self.stock = None
        self.view = None
        self.summary = tk.StringVar(value="게임의 시즌 합성 재료를 확인합니다.")
        ttk.Label(self, text="시즌 합성 재료", font=("Malgun Gothic", 15, "bold")).pack(anchor="w")
        self.recipe = ttk.Combobox(self, state="readonly")
        self.recipe.pack(fill="x", pady=(10, 4))
        self.recipe.bind("<<ComboboxSelected>>", lambda _: self.draw())
        ttk.Label(self, textvariable=self.summary, wraplength=640).pack(anchor="w", pady=(0, 8))
        self.grid_view = CardGrid(self)
        self.grid_view.pack(fill="both", expand=True)
        self.after(200, self.poll)

    def refresh(self):
        if self.loading or not self.winfo_viewable():
            return
        install = Path(self.game_path.get())
        game = self.game if install == self.install else None
        if game is None:
            self.summary.set("설치된 게임에서 시즌 레시피와 이미지를 읽는 중…")
        self.loading = True
        Thread(target=self.read, args=(install, game), daemon=True).start()

    def read(self, install, game):
        try:
            new_game = game is None
            if new_game:
                game = GameData(install=install)
            rows, _ = game.rows()
            art = load_season_art(game, install) if new_game else None
            stock = Counter()
            for name, _, stars, count in rows:
                stock[name, stars] += count
            self.result.put((install, game, art, stock, None))
        except Exception as error:
            self.result.put((install, None, None, None, str(error)))

    def poll(self):
        try:
            install, game, art, stock, error = self.result.get_nowait()
        except Empty:
            self.after(200, self.poll)
            return
        self.loading = False
        if install == Path(self.game_path.get()):
            if error is not None:
                self.stock = self.view = None
                self.grid_view.set_cards([])
                self.summary.set(f"게임 데이터를 읽을 수 없습니다 · {error}")
            else:
                self.install, self.game, self.stock = install, game, stock
                if art is not None:
                    self.variants, images = art
                    self.images = {key: tk.PhotoImage(master=self, data=data) for key, data in images.items()}
                    self.recipes = sorted(game.recipes, key=lambda key: (-key[0], key[1], key[2]))
                    self.recipe["values"] = [f"시즌 {s} · {name} · {stars}성" for s, name, stars in self.recipes]
                    if self.recipes:
                        self.recipe.current(0)
                    self.view = None
                self.draw()
        self.after(200, self.poll)
        self.after(1800, self.refresh)

    def draw(self):
        if self.stock is None:
            return
        index = self.recipe.current()
        if index < 0:
            self.summary.set("설치본에서 시즌 합성 레시피를 찾지 못했습니다.")
            self.grid_view.set_cards([])
            return
        recipe = self.game.recipes[self.recipes[index]]
        counts = tuple(min(need, self.stock[key]) for key, need in recipe.items())
        view = (index, counts)
        if view == self.view:
            return
        reset = self.view is None or self.view[0] != index
        self.view = view
        cards = []
        for ((name, stars), need), have in zip(recipe.items(), counts):
            rarity, variant = self.variants.get((name, stars), (0, None))
            for slot in range(need):
                owned = slot < have
                cards.append((name, rarity, stars, "보유" if owned else "부족",
                              self.images.get((variant, owned)), owned))
        self.summary.set(f"보유 재료 {sum(counts)} / {sum(recipe.values())}마리 · 컬러 보유 / 흑백 부족")
        self.grid_view.set_cards(cards, reset)
