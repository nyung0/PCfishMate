from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import json
import time
from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import ttk, font as tkfont

import UnityPy
from PIL import Image, ImageOps, ImageTk

from ocean_ui import CardGrid, OCEAN, PANEL, WATER, TEXT, ACCENT, MUTED, RARITIES
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
        "Icon_BreedHeart": 16, "Icon_LvUp": 16, "Icon_Merge": 16, "Icon_Config": 24, "Frame_AquaWindow": 64,
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
        if (size == 16 or name == "Icon_Config") and (bounds := icon.getbbox()):
            icon = icon.crop(bounds)
        if name == "Icon_Config":
            icon = ImageOps.contain(icon, (size, size), Image.Resampling.NEAREST)
        else:
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
    materials.update((name, stars) for _, name, stars in game.recipes)
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


def season_material_stock(game, state):
    stock = Counter()
    exhausted = Counter()
    for fish in state["fishList"]:
        variant = game.variants.get(fish["Fish"])
        if variant is None:
            species, stars = fish["Type"], fish["Grade"]
        else:
            species, _, stars = variant
        key = game.names.get(species, species), stars
        stock[key] += 1
        if fish.get("BreedCount") == 0:
            exhausted[key] += 1
    return stock, exhausted


class SeasonTab(ttk.Frame):
    def __init__(self, parent, game_path, catalog, *, ready_enabled=False, save_ready=None, notify_ready=None):
        super().__init__(parent, padding=16)
        self.game_path = game_path
        self.catalog = catalog
        self.ready_notifications = tk.BooleanVar(value=ready_enabled)
        self.save_ready = save_ready
        self.notify_ready = notify_ready
        self.ready_recipes = set()
        self.material_variants = defaultdict(list)
        self.visible_materials = []
        self.result = Queue()
        self.loading = False
        self.install = None
        self.game = None
        self.recipes = []
        self.stock = None
        self.exhausted_stock = Counter()
        self.view = None
        self.selected_recipe = None
        self.selector_images = []
        self.season_title = tk.StringVar(value="시즌 합성")
        self.season_status = tk.StringVar(value="현재 시즌 확인 중 · 게임 연결이 필요해요.")
        self.summary = tk.StringVar(value="게임의 시즌 합성 재료를 확인합니다.")
        heading = ttk.Frame(self)
        heading.pack(fill="x")
        title_font = ("Malgun Gothic", 15, "bold")
        time_font = ("Malgun Gothic", 9)
        title_descent = tkfont.Font(root=self, font=title_font).metrics("descent")
        time_descent = tkfont.Font(root=self, font=time_font).metrics("descent")
        tk.Label(heading, textvariable=self.season_title, font=title_font,
                 bg=OCEAN, fg=TEXT, borderwidth=0, padx=0, pady=0).grid(row=0, column=0, sticky="s")
        tk.Label(heading, textvariable=self.season_status, font=time_font,
                 bg=OCEAN, fg=MUTED, borderwidth=0, padx=0, pady=0).grid(
                     row=0, column=1, sticky="s", padx=(12, 0),
                     pady=(0, max(0, title_descent - time_descent)),
                 )
        tk.Frame(self, bg=WATER, height=2).pack(fill="x", pady=(6, 0))
        self.grade_images = {}
        self.fish_selectors = ttk.Frame(self)
        self.fish_selectors.pack(fill="x", pady=(5, 3))
        tk.Frame(self, bg=PANEL, height=1).pack(fill="x", pady=(0, 3))
        self.star_selector = tk.Frame(self, bg=OCEAN)
        self.star_selector.pack(anchor="center", pady=(0, 5))
        tk.Frame(self, bg=WATER, height=2).pack(fill="x", pady=(0, 4))
        notification_options = ttk.Frame(self)
        notification_options.pack(fill="x", pady=(1, 3))
        self.zero_breeds_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(notification_options, text="남은 교배 0회 재료만", variable=self.zero_breeds_only,
                        style="Ocean.TCheckbutton", command=self.draw).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(notification_options, text="합성 가능 알림", variable=self.ready_notifications,
                        style="Ocean.TCheckbutton", command=self.toggle_ready_notifications).pack(side="left")
        ttk.Label(self, textvariable=self.summary, wraplength=640).pack(anchor="w", pady=(0, 4))
        self.grid_view = CardGrid(self, card_width=110, toggle_favorite=self.toggle_favorite)
        self.grid_view.pack(fill="both", expand=True)
        self.after(200, self.poll)
        self.after(1800, self.refresh_periodically)

    def refresh_periodically(self):
        self.refresh()
        self.after(1800, self.refresh_periodically)

    def refresh(self):
        if self.loading or (not self.winfo_viewable() and not self.ready_notifications.get()):
            return
        self.draw()
        install = Path(self.game_path.get())
        game = self.game if install == self.install else None
        if game is not None:
            try:
                stamp = (install / "PCFish_Data/resources.assets").stat().st_mtime_ns
                if stamp != getattr(game, "_season_resource_stamp", None):
                    game = None
            except OSError:
                game = None
        if game is None:
            self.summary.set("설치된 게임에서 시즌 레시피와 이미지를 읽는 중…")
        self.loading = True
        Thread(target=self.read, args=(install, game), daemon=True).start()

    def read(self, install, game):
        try:
            new_game = game is None
            if new_game:
                stamp = (install / "PCFish_Data/resources.assets").stat().st_mtime_ns
                game = GameData(install=install)
                game._season_resource_stamp = stamp
            state = game.state()
            art = load_season_art(game, install) if new_game else None
            stock, exhausted = season_material_stock(game, state)
            grade_icon = load_grade_icon(install) if new_game else None
            self.result.put((install, game, art, (stock, exhausted), grade_icon, None))
        except Exception as error:
            self.result.put((install, None, None, None, None, str(error)))

    def poll(self):
        try:
            install, game, art, stock, grade_icon, error = self.result.get_nowait()
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
                self.install, self.game = install, game
                self.stock, self.exhausted_stock = stock
                if grade_icon is not None:
                    with Image.open(BytesIO(grade_icon)) as source:
                        colored = source.convert("RGBA")
                    colored = ImageOps.contain(colored, (24, 24), Image.Resampling.NEAREST)
                    gray = ImageOps.grayscale(colored).convert("RGBA")
                    gray.putalpha(colored.getchannel("A"))
                    self.grade_images = {
                        True: ImageTk.PhotoImage(colored, master=self),
                        False: ImageTk.PhotoImage(gray, master=self),
                    }
                if art is not None:
                    self.variants, images = art
                    self.material_variants.clear()
                    for variant, (species, _, stars) in game.variants.items():
                        self.material_variants[game.names.get(species, species), stars].append(variant)
                    self.images = {key: tk.PhotoImage(master=self, data=data) for key, data in images.items()}
                    self.recipes = []
                    self.view = None
                self.draw()
        self.after(200, self.poll)

    def toggle_favorite(self, index):
        if 0 <= index < len(self.visible_materials):
            self.catalog.toggle_variants(self.visible_materials[index])
            self.draw()

    def update_season(self):
        season = None
        path = self.catalog.favorites_path.with_name("status.json")
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - path.stat().st_mtime < 5 and Path(status["GameRoot"]).resolve() == Path(self.game_path.get()).resolve():
                candidate = status["Season"]
                if (isinstance(candidate, dict) and type(candidate.get("Id")) is int
                        and type(candidate.get("Start")) is int and type(candidate.get("Finish")) is int):
                    season = candidate
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
        now = int(time.time())
        if season is None:
            self.season_title.set("시즌 합성")
            self.season_status.set("최신 모듈과 게임 연결이 필요해요.")
            recipes = []
        elif season["Start"] <= now < season["Finish"]:
            end = datetime.fromtimestamp(season["Finish"], timezone(timedelta(hours=9)))
            self.season_title.set(f"시즌 {season['Id']}")
            self.season_status.set(f"{end:%Y-%m-%d %H:%M} 종료 예정 (한국시간)")
            recipes = sorted(
                (key for key in self.game.recipes if key[0] == season["Id"]),
                key=lambda key: (self.variants.get((key[1], key[2]), (len(RARITIES), None))[0],
                                 key[1], key[2]),
            )
        else:
            self.season_title.set(f"시즌 {season['Id']}")
            self.season_status.set("현재 진행 중인 시즌이 없어요.")
            recipes = []
        if recipes != self.recipes:
            self.recipes = recipes
            if self.selected_recipe not in recipes:
                self.selected_recipe = recipes[0] if recipes else None
            self.build_fish_selectors()
            self.view = None

    def select_recipe(self, name, stars):
        self.focus_set()
        self.selected_recipe = next(
            (key for key in self.recipes if key[1:] == (name, stars)), self.selected_recipe,
        )
        self.build_fish_selectors()
        self.draw()

    def build_fish_selectors(self):
        signature = tuple(self.recipes), id(getattr(self, "images", None)), tuple(self.grade_images.values())
        if signature == getattr(self, "selector_signature", None):
            self.update_selector_selection()
            return
        self.selector_signature = signature
        for child in self.fish_selectors.winfo_children():
            child.destroy()
        for column in range(self.fish_selectors.grid_size()[0]):
            self.fish_selectors.columnconfigure(column, weight=0, minsize=0, uniform="")
        self.selector_images = []
        names = list(dict.fromkeys(name for _, name, _ in self.recipes))
        for column, name in enumerate(names):
            self.fish_selectors.columnconfigure(column, weight=1, uniform="fish")
            selected = self.selected_recipe is not None and self.selected_recipe[1] == name
            frame = tk.Frame(self.fish_selectors, bg=OCEAN)
            frame.grid(row=0, column=column, sticky="n", padx=6)
            grades = sorted(stars for _, target, stars in self.recipes if target == name)
            _, variant = self.variants.get((name, grades[0]), (0, None))
            image = self.images.get((variant, True))
            if image is not None:
                self.selector_images.append(image)
            chosen_grade = self.selected_recipe[2] if self.selected_recipe else 1
            grade = chosen_grade if chosen_grade in grades else grades[0]
            button = tk.Button(
                frame, image=image or "", text=name, compound="top", bg=PANEL if selected else OCEAN,
                activebackground=WATER, fg=TEXT, activeforeground=TEXT, relief="flat",
                borderwidth=0, highlightthickness=0, takefocus=False, default="disabled",
                font=("Malgun Gothic", 9, "bold"), padx=6, pady=1, cursor="hand2",
                command=lambda target=name, stars=grade: self.select_recipe(target, stars),
            )
            button.pack()
        for child in self.star_selector.winfo_children():
            child.destroy()
        if self.selected_recipe is None:
            return
        _, selected_name, selected_grade = self.selected_recipe
        grades = {stars for _, name, stars in self.recipes if name == selected_name}
        ttk.Style(self).configure("SeasonArrow.TButton", font=("Malgun Gothic", 9), padding=(4, 1))
        lower = max((grade for grade in grades if grade < selected_grade), default=None)
        higher = min((grade for grade in grades if grade > selected_grade), default=None)
        ttk.Button(
            self.star_selector, text="◀", width=2, style="SeasonArrow.TButton",
            state="normal" if lower is not None else "disabled",
            command=lambda: self.select_recipe(selected_name, lower),
        ).pack(side="left", padx=(0, 6))
        for stars in range(1, 6):
            filled = stars <= selected_grade
            image = self.grade_images.get(filled)
            tk.Button(
                self.star_selector, image=image or "", text="" if image else "★",
                font=("Segoe UI Symbol", 16), padx=2, pady=0,
                bg=OCEAN, activebackground=OCEAN, fg=ACCENT if filled else MUTED,
                activeforeground=ACCENT, relief="flat", borderwidth=0, cursor="hand2",
                state="normal" if stars in grades else "disabled",
                command=lambda count=stars: self.select_recipe(selected_name, count),
            ).pack(side="left", padx=2)
        ttk.Button(
            self.star_selector, text="▶", width=2, style="SeasonArrow.TButton",
            state="normal" if higher is not None else "disabled",
            command=lambda: self.select_recipe(selected_name, higher),
        ).pack(side="left", padx=(6, 0))

    def update_selector_selection(self):
        if self.selected_recipe is None:
            return
        _, selected_name, selected_grade = self.selected_recipe
        for frame in self.fish_selectors.winfo_children():
            button = frame.winfo_children()[0]
            name = button.cget("text")
            selected = name == selected_name
            grades = sorted(grade for _, target, grade in self.recipes if target == name)
            grade = selected_grade if selected_grade in grades else grades[0]
            button.configure(
                bg=PANEL if selected else OCEAN,
                command=lambda target=name, stars=grade: self.select_recipe(target, stars),
            )
        grades = {grade for _, name, grade in self.recipes if name == selected_name}
        widgets = self.star_selector.winfo_children()
        for index, stars in ((0, max((g for g in grades if g < selected_grade), default=None)),
                             (-1, min((g for g in grades if g > selected_grade), default=None))):
            widgets[index].configure(
                state="normal" if stars is not None else "disabled",
                command=lambda count=stars: self.select_recipe(selected_name, count),
            )
        for count, button in enumerate(widgets[1:-1], 1):
            filled = count <= selected_grade
            image = self.grade_images.get(filled)
            button.configure(
                image=image or "", text="" if image else "★", fg=ACCENT if filled else MUTED,
                state="normal" if count in grades else "disabled",
                command=lambda stars=count: self.select_recipe(selected_name, stars),
            )

    def toggle_ready_notifications(self):
        enabled = self.ready_notifications.get()
        if self.save_ready is not None and not self.save_ready(enabled):
            self.ready_notifications.set(not enabled)
            return
        self.ready_recipes.clear()
        self.refresh()

    def check_ready_notifications(self):
        if not self.ready_notifications.get():
            self.ready_recipes.clear()
            return
        ready = {key for key in self.recipes
                 if all(self.stock[material] >= need for material, need in self.game.recipes[key].items())}
        newly_ready = ready - self.ready_recipes
        self.ready_recipes = ready
        notifications = []
        for _, name, stars in sorted(newly_ready):
            base_grade = min(grade for _, target, grade in self.recipes if target == name)
            _, variant = self.variants.get((name, base_grade), (0, None))
            if variant:
                notifications.append((variant, name, stars))
        if notifications and self.notify_ready is not None:
            self.notify_ready(Path(self.game_path.get()), notifications)

    def draw(self):
        if self.stock is None:
            return
        self.update_season()
        self.check_ready_notifications()
        if self.selected_recipe is None:
            self.summary.set("진행 중인 시즌의 합성 재료만 표시합니다.")
            self.grid_view.set_cards([])
            return
        recipe = self.game.recipes[self.selected_recipe]
        zero_only = self.zero_breeds_only.get()
        stock = self.exhausted_stock if zero_only else self.stock
        counts = tuple(min(need, stock[key]) for key, need in recipe.items())
        marks = tuple(any(variant in self.catalog.favorites for variant in self.material_variants.get(key, ()))
                      for key in recipe)
        notice = self.catalog.favorites_status.get()
        view = (self.selected_recipe, counts, marks, notice, zero_only)
        if view == self.view:
            return
        reset = self.view is None or self.view[0] != self.selected_recipe
        self.view = view
        cards = []
        favorites = []
        self.visible_materials = []
        for ((name, stars), need), have, marked in zip(recipe.items(), counts, marks):
            rarity, variant = self.variants.get((name, stars), (0, None))
            variants = self.material_variants.get((name, stars), ())
            for slot in range(need):
                owned = slot < have
                cards.append((name, rarity, stars, "보유" if owned else "부족",
                              self.images.get((variant, owned)), owned))
                favorites.append(marked if variants else None)
                self.visible_materials.append(variants)
        summary = f"보유 재료 {sum(counts)} / {sum(recipe.values())}마리 · 컬러 보유 / 흑백 부족"
        if zero_only:
            summary += " · 남은 교배 0회 기준"
        if notice:
            summary += f"\n{notice}"
        self.summary.set(summary)
        self.grid_view.set_cards(cards, reset, favorites=favorites)
