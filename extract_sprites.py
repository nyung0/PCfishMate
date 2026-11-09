import re
from pathlib import Path

import UnityPy
from PIL import Image, ImageOps

from pc_fish_data import INSTALL

output = Path(__file__).resolve().parent / "fish_sprites"
output.mkdir(exist_ok=True)
assets = UnityPy.load(str(INSTALL / "PCFish_Data/sharedassets0.assets"))
count = 0
for obj in assets.objects:
    if obj.type.name != "Sprite":
        continue
    name = obj.peek_name()
    if not re.fullmatch(r"FS\d{5}_\d{2}_\d{2}_\d{2}", name):
        continue
    fish = obj.read().image.convert("RGBA")
    fish = fish.crop(fish.getbbox())
    fish.thumbnail((62, 42), Image.Resampling.NEAREST)
    key = name
    fish.save(output / f"{key}.png")
    gray = ImageOps.grayscale(fish).convert("RGBA")
    gray.putalpha(fish.getchannel("A"))
    gray.save(output / f"{key}-gray.png")
    count += 1
print(f"Extracted {count} fish variants")
