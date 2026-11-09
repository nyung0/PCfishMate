"""Read the local PC FISH catalog and save without changing either file."""
import ctypes as c
import hashlib
import json
import re
import struct
from pathlib import Path

INSTALL = Path(r"C:\Program Files (x86)\Steam\steamapps\common\PC FISH")
SAVE = Path.home() / "AppData/LocalLow/NANOO COMPANY Inc_/PCFish/SaveFile.es3"


def aes_decrypt(key, iv, data):
    dll = c.WinDLL("bcrypt")
    ptr = c.c_void_p
    num = c.c_ulong
    outnum = c.POINTER(num)
    outptr = c.POINTER(ptr)
    signatures = {
        "BCryptOpenAlgorithmProvider": [outptr, c.c_wchar_p, c.c_wchar_p, num],
        "BCryptSetProperty": [ptr, c.c_wchar_p, ptr, num, num],
        "BCryptGenerateSymmetricKey": [ptr, outptr, ptr, num, ptr, num, num],
        "BCryptDecrypt": [ptr, ptr, num, ptr, ptr, num, ptr, num, outnum, num],
        "BCryptDestroyKey": [ptr],
        "BCryptCloseAlgorithmProvider": [ptr, num],
    }
    for name, args in signatures.items():
        function = getattr(dll, name)
        function.argtypes = args
        function.restype = c.c_long

    def check(status):
        if status:
            raise ValueError("게임 저장 파일을 해석할 수 없습니다.")

    algorithm, handle = ptr(), ptr()
    check(dll.BCryptOpenAlgorithmProvider(c.byref(algorithm), "AES", None, 0))
    try:
        mode = c.create_unicode_buffer("ChainingModeCBC")
        check(dll.BCryptSetProperty(algorithm, "ChainingMode", mode, c.sizeof(mode), 0))
        check(dll.BCryptGenerateSymmetricKey(algorithm, c.byref(handle), None, 0, key, len(key), 0))
        try:
            output = c.create_string_buffer(len(data))
            iv_buffer = c.create_string_buffer(iv, len(iv))
            size = num()
            check(dll.BCryptDecrypt(handle, data, len(data), None, iv_buffer, len(iv),
                                    output, len(output), c.byref(size), 1))
            return output.raw[:size.value]
        finally:
            dll.BCryptDestroyKey(handle)
    finally:
        dll.BCryptCloseAlgorithmProvider(algorithm, 0)


def asset_string(data, offset):
    size, = struct.unpack_from("<I", data, offset)
    if size > 1024:
        raise ValueError("지원하지 않는 게임 리소스 형식입니다.")
    return data[offset + 4:offset + 4 + size].decode("utf-8"), offset + 4 + ((size + 3) & ~3)


class GameData:
    def __init__(self, install=INSTALL, save=SAVE):
        self.save = save
        assets = (install / "PCFish_Data/resources.assets").read_bytes()
        defaults = re.search(rb"ES3Defaults\x00.{0,128}?SaveFile\.es3.{1,24}?([!-~]{10,40})",
                             assets, re.DOTALL)
        if not defaults:
            raise ValueError("설치본에서 저장 형식 정보를 찾지 못했습니다.")
        self._key_material = defaults[1]
        self.names = {}
        for match in re.finditer(rb"Fish/(fs\d{5})\x00{0,3}\x00\x00\x00\x00\x0f\x00\x00\x00", assets):
            try:
                _, offset = asset_string(assets, match.end())
                korean, _ = asset_string(assets, offset)
                if korean:
                    self.names[match[1].decode().upper()] = korean
            except (UnicodeError, ValueError, struct.error):
                continue
        self.variants = {}
        for match in re.finditer(rb"(FS\d{5}_\d{2}_\d{2}_\d{2}),(FS\d{5}),(\d),(\d),\d", assets):
            variant, species = (part.decode() for part in match.groups()[:2])
            self.variants[variant] = (species, int(match[3]), int(match[4]))
        if not self.variants:
            raise ValueError("설치본에서 도감 데이터를 찾지 못했습니다.")
        upgrade_tables = re.findall(
            rb"tank_level_v(\d+)\.(\d+)\x00.{4}level,requireExp,exp,[^\r\n]+\r?\n"
            rb"((?:[0-9][^\x00\r\n]+\r?\n?)+)", assets, re.DOTALL)
        upgrade_table = max(upgrade_tables, key=lambda table: (int(table[0]), int(table[1])),
                            default=(b"0", b"0", b""))[2]
        self.upgrades = {
            int(level): int(total) + int(cost)
            for level, cost, total in re.findall(
                rb"(?m)^(\d{1,2}),(\d+),(\d+),\d+,\d+,(?:BF\d+|none),DECO_00_\d+",
                upgrade_table)
            if int(cost) > 0 and int(level) < 10
        }
        self.recipes = {}
        for table in re.findall(rb"recipeFishGrade,recipeAmount\r?\n((?:[0-9][^\x00\r\n]+\r?\n?)+)", assets):
            for season, target, stars, material, grade, qty in re.findall(
                    rb"(?m)^(\d+),(FS\d{5}),(\d+),(FS\d{5}),(\d+),(\d+)", table):
                name = self.names.get(target.decode(), target.decode())
                item = material.decode()
                self.recipes.setdefault((int(season), name, int(stars)), {})[
                    (self.names.get(item, item), int(grade))] = int(qty)
        self._stamp = None
        self._state = None

    def state(self):
        stamp = self.save.stat().st_mtime_ns
        if stamp != self._stamp:
            data = self.save.read_bytes()
            iv = data[:16]
            key = hashlib.pbkdf2_hmac("sha1", self._key_material, iv, 100, 16)
            raw = aes_decrypt(key, iv, data[16:]).decode("utf-8")
            # ES3 emits unquoted integer keys in the achievement dictionary.
            raw = re.sub(r'("achievementCount"\s*:\s*\{)([^}]*)(\})',
                         lambda m: m[1] + re.sub(r'(\d+)\s*:', r'"\1":', m[2]) + m[3], raw)
            state = json.loads(raw)["Basic"]["value"]
            if not isinstance(state["fishList"], list):
                raise ValueError("지원하지 않는 보유 목록 형식입니다.")
            self._state, self._stamp = state, stamp
        return self._state

    def can_upgrade(self, state):
        target = self.upgrades.get(state["tankLevel"])
        return target is not None and state["tankExp"] >= target

    def rows(self):
        state = self.state()
        rows = {(self.names.get(species, species), rarity, stars): 0
                for species, rarity, stars in self.variants.values()}
        remaining = {}
        for fish in state["fishList"]:
            species, rarity, stars = self.variants.get(
                fish["Fish"], (fish["Type"], fish["Level"], fish["Grade"]))
            row = self.names.get(species, species), rarity, stars
            rows[row] = rows.get(row, 0) + 1
            left = max(0, fish.get("BreedCount", 0))
            remaining[row] = remaining.get(row, 0) + left
        return [list(row) + [amount] for row, amount in sorted(rows.items())], remaining
