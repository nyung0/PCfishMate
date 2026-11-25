import base64
import re
from collections import defaultdict
from colorsys import rgb_to_hsv
from dataclasses import dataclass
from hashlib import sha256

import UnityPy
from PIL import Image

from pc_fish_data import GameData


SPRITE_ID = re.compile(r"(FS[0-9]{5}_[0-9]{2}_[0-9]{2}_[0-9]{2})(?:_([0-9]{1,2}))?")


@dataclass(frozen=True, order=True)
class Variant:
    grade: int
    rarity: int
    color: int
    sprite_id: str


@dataclass(frozen=True)
class TextureRegion:
    name: str
    atlas_width: int
    atlas_height: int
    left: int
    bottom: int
    width: int
    height: int

    @property
    def atlas(self):
        return self.name, self.atlas_width, self.atlas_height

    def patch(self, original, edited):
        return {"X": self.left, "Y": self.bottom, "Width": self.width, "Height": self.height,
                "Original": self.encode(original), "Pixels": self.encode(edited)}

    def read_patch(self, record, original):
        if (record.get("X"), record.get("Y"), record.get("Width"), record.get("Height")) != (
                self.left, self.bottom, self.width, self.height):
            raise ValueError("저장한 물고기 이미지 위치가 달라요.")
        edited = self.decode(record["Pixels"])
        if edited.getchannel("A").tobytes() != original.getchannel("A").tobytes():
            raise ValueError("저장한 물고기의 투명도가 원본과 달라요.")
        return edited

    def encode(self, image):
        if image.mode != "RGBA" or image.size != (self.width, self.height):
            raise ValueError("편집한 물고기 이미지 크기가 일치하지 않아요.")
        return base64.b64encode(image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()).decode("ascii")

    def decode(self, data):
        pixels = base64.b64decode(data, validate=True)
        if len(pixels) != self.width * self.height * 4:
            raise ValueError("저장한 물고기 이미지 크기가 일치하지 않아요.")
        image = Image.frombytes("RGBA", (self.width, self.height), pixels)
        return image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)


def original_texture(game, resource, metadata):
    # FishColorStudio used to edit the installed texture and saved a backup per Steam build.
    manifest = game.resolve().parent.parent / "appmanifest_4842670.acf"
    try:
        build = re.search(rb'"buildid"\s+"(\d+)"', manifest.read_bytes())
    except OSError:
        build = None
    build_id = build[1].decode("ascii") if build else "local"
    digest = sha256(metadata).hexdigest()[:16]
    backup = resource.with_name(f"{resource.name}.fishcolor-{build_id}-{digest}.bak")
    if not backup.is_file():
        return resource
    if backup.stat().st_size != resource.stat().st_size:
        raise ValueError("원본 이미지 백업의 크기가 현재 설치본과 달라요.")
    return backup


class FishColorLibrary:
    def __init__(self, game):
        data = GameData(install=game)
        self.names = data.names
        folder = game / "PCFish_Data"
        self.resource = folder / "sharedassets0.assets.resS"
        # Store metadata in memory. Texture reads below hold the file only for the duration of the read.
        metadata = (folder / "sharedassets0.assets").read_bytes()
        self.original_resource = original_texture(game, self.resource, metadata)
        self.assets = UnityPy.load(metadata)
        self.sprites = {}
        self.species = defaultdict(list)
        self.frames = defaultdict(dict)
        self.renders = {}
        self.cache = {}
        for obj in self.assets.objects:
            if obj.type.name != "Sprite":
                continue
            name = obj.peek_name()
            match = SPRITE_ID.fullmatch(name or "")
            if match is None or match[1] not in data.variants or name in self.sprites:
                continue
            self.sprites[name] = obj
            if match[2] is not None:
                self.frames[match[1]][match[2]] = name
                continue
            species, rarity, grade = data.variants[name]
            self.species[species].append(Variant(grade, rarity, int(name.rsplit("_", 1)[1]), name))
        if not self.species:
            raise ValueError("설치 폴더에서 물고기 이미지를 찾지 못했어요.")
        for variants in self.species.values():
            variants.sort()

    def name(self, species):
        return self.names.get(species, species)

    def image(self, sprite_id):
        if sprite_id not in self.cache:
            self.cache[sprite_id] = self.read_image(sprite_id)
        return self.cache[sprite_id]

    def color_patch(self, sprite_id, edited):
        original, region = self.image(sprite_id)
        patch = region.patch(original, edited)
        frame_images = {number: self.image(name) for number, name in self.frames.get(sprite_id, {}).items()}
        if frame_images:
            if any(frame_region.atlas != region.atlas for _, frame_region in frame_images.values()):
                raise ValueError("이 물고기의 수조 애니메이션이 다른 이미지 묶음을 사용해요.")
            images = [image for image, _ in frame_images.values()]
            patch["Frames"] = {
                number: frame_region.patch(image, updated)
                for (number, (image, frame_region)), updated in zip(
                    frame_images.items(), recolor_frames(original, edited, images),
                )
            }
        return patch

    def read_image(self, sprite_id):
        sprite = self.sprites[sprite_id].read()
        if not sprite.m_SpriteAtlas:
            raise ValueError("이 물고기는 실시간 색상 적용을 지원하지 않아요.")
        atlas_id = sprite.m_SpriteAtlas.path_id
        if atlas_id not in self.renders:
            atlas = sprite.m_SpriteAtlas.read()
            if atlas.m_Name != "AtlasFish":
                raise ValueError("물고기 이미지 묶음을 확인할 수 없어요.")
            self.renders[atlas_id] = dict(atlas.m_RenderDataMap)
        render = self.renders[atlas_id].get(sprite.m_RenderDataKey)
        if render is None or (render.settingsRaw & 0x3F) != 3 or render.alphaTexture:
            raise ValueError("이 물고기의 이미지 배치는 실시간 색상 적용을 지원하지 않아요.")
        texture = render.texture.read()
        stream = texture.m_StreamData
        width, height = texture.m_Width, texture.m_Height
        if (texture.m_TextureFormat != 4 or texture.m_MipCount != 1 or
                stream is None or stream.path != self.resource.name or stream.size != width * height * 4):
            raise ValueError("이 게임 버전의 물고기 이미지 형식은 실시간 색상 적용을 지원하지 않아요.")
        rect = render.textureRect
        left, bottom = round(rect.x), round(rect.y)
        right, top = round(rect.x + rect.width), round(rect.y + rect.height)
        if not (0 <= left < right <= width and 0 <= bottom < top <= height):
            raise ValueError("물고기 이미지 위치를 확인할 수 없어요.")
        rows = []
        with self.original_resource.open("rb") as resource:
            for y in range(top - 1, bottom - 1, -1):
                resource.seek(stream.offset + (y * width + left) * 4)
                rows.append(resource.read((right - left) * 4))
        full = Image.frombytes("RGBA", (right - left, top - bottom), b"".join(rows))
        bounds = full.getbbox()
        if not bounds:
            raise ValueError("물고기에 표시할 이미지가 없어요.")
        x0, y0, x1, y1 = bounds
        region = TextureRegion(texture.m_Name, width, height, left + x0, top - y1, x1 - x0, y1 - y0)
        return full.crop(bounds), region


def line_pixels(start, end):
    x, y = start
    end_x, end_y = end
    dx, dy = abs(end_x - x), -abs(end_y - y)
    step_x, step_y = (1 if end_x > x else -1), (1 if end_y > y else -1)
    error = dx + dy
    while True:
        yield x, y
        if (x, y) == end:
            return
        twice = 2 * error
        if twice >= dy:
            error += dy
            x += step_x
        if twice <= dx:
            error += dx
            y += step_y


def paint_stroke(image, original, start, end, size, color):
    """Paint a continuous pixel stroke, clipping every brush stamp to the original sprite."""
    result, mask = image.load(), original.load()
    inset = (size - 1) // 2
    changed = False
    for x, y in line_pixels(start, end):
        left, top = x - inset, y - inset
        for py in range(max(0, top), min(image.height, top + size)):
            for px in range(max(0, left), min(image.width, left + size)):
                alpha = mask[px, py][3]
                if alpha and result[px, py][:3] != color:
                    result[px, py] = (*color, alpha)
                    changed = True
    return changed


def replace_color(image, original, point, color):
    """Fill the connected area of the clicked RGB within the original sprite."""
    target = image.getpixel(point)[:3]
    if not original.getpixel(point)[3] or target == color:
        return image
    result = image.copy()
    pixels, mask = result.load(), original.load()
    pixels[point] = (*color, mask[point][3])
    pending = [point]
    while pending:
        x, y = pending.pop()
        for px, py in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= px < image.width and 0 <= py < image.height:
                alpha = mask[px, py][3]
                if alpha and pixels[px, py][:3] == target:
                    pixels[px, py] = (*color, alpha)
                    pending.append((px, py))
    return result


def brightness(pixel):
    return (299 * pixel[0] + 587 * pixel[1] + 114 * pixel[2]) / 1000


@dataclass(frozen=True)
class ColorGroup:
    anchor: tuple[int, int]
    points: frozenset[tuple[int, int]]


def sprite_palette(image):
    """Group original shades by hue, even when they appear in disconnected parts."""
    by_color = defaultdict(list)
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, alpha = pixels[x, y]
            if alpha:
                by_color[r, g, b].append((x, y))

    neutral = defaultdict(list)
    hues = []
    for rgb in by_color:
        if max(rgb) - min(rgb) < 14:
            light = brightness(rgb)
            neutral[0 if light < 48 else 1 if light < 190 else 2].append(rgb)
        else:
            hue, _, _ = rgb_to_hsv(*(channel / 255 for channel in rgb))
            hues.append((hue * 360, rgb))

    families = list(neutral.values())
    hues.sort()
    if hues:
        # Start just after the largest hue gap so reds can group across 0°.
        start = max(range(len(hues)), key=lambda i: (hues[i][0] - hues[i - 1][0]) % 360)
        ordered = hues[start:] + hues[:start]
        first_hue = ordered[0][0]
        family = []
        for hue, rgb in ordered:
            if (hue - first_hue) % 360 > 36:
                families.append(family)
                family = []
                first_hue = hue
            family.append(rgb)
        families.append(family)

    groups = []
    for colors in families:
        points = frozenset(point for rgb in colors for point in by_color[rgb])
        representative = max(colors, key=lambda rgb: len(by_color[rgb]))
        groups.append(ColorGroup(by_color[representative][0], points))
    return sorted(groups, key=lambda group: -len(group.points))


def recolor(original, current, area, seed, color):
    """Map the clicked shade to the chosen RGB, preserving shadows and alpha."""
    updated = current.copy()
    source = original.load()
    result = updated.load()
    pivot = brightness(source[seed])
    for point in area:
        level = brightness(source[point])
        if level < pivot:
            rgb = tuple(round(channel * level / pivot) for channel in color)
        elif level > pivot:
            ratio = (level - pivot) / (255 - pivot)
            rgb = tuple(round(channel + (255 - channel) * ratio) for channel in color)
        else:
            rgb = color
        result[point] = (*rgb, source[point][3])
    return updated


def recolor_frames(original, edited, frames):
    """Transfer a color edit to the original pixels of each animation pose."""
    source = original.get_flattened_data()
    replacement = edited.get_flattened_data()
    if source == replacement:
        yield from frames
        return

    by_shade = defaultdict(list)
    for index, (before, after) in enumerate(zip(source, replacement)):
        if before[3]:
            by_shade[before[:3]].append((index % original.width, index // original.width, after[:3]))
    uniform = {}
    for shade, pixels in by_shade.items():
        first = pixels[0][2]
        if all(rgb == first for _, _, rgb in pixels):
            uniform[shade] = first

    def distance(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b))

    nearest = {}
    for frame in frames:
        pixels = frame.get_flattened_data()
        if frame.size == original.size and pixels == source:
            yield edited
            continue
        updated = list(pixels)
        scale_x, scale_y = original.width / frame.width, original.height / frame.height
        for index, (red, green, blue, alpha) in enumerate(pixels):
            if not alpha:
                continue
            rgb = red, green, blue
            shade = rgb
            if shade not in by_shade:
                if rgb not in nearest:
                    match = min(by_shade, key=lambda color: distance(color, rgb))
                    # Keep distinct colors drawn only in this frame, such as an effect or highlight.
                    nearest[rgb] = match if distance(match, rgb) <= 32 ** 2 else None
                shade = nearest[rgb]
                if shade is None:
                    continue
            mapped = uniform.get(shade)
            if mapped is None:
                # A partial edit may change one area while leaving the same shade elsewhere.
                x = (index % frame.width + 0.5) * scale_x - 0.5
                y = (index // frame.width + 0.5) * scale_y - 0.5
                _, _, mapped = min(by_shade[shade], key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
            if mapped != shade:
                color = tuple(max(0, min(255, target + value - base))
                              for target, value, base in zip(mapped, rgb, shade))
                updated[index] = (*color, alpha)
        result = frame.copy()
        result.putdata(updated)
        yield result
