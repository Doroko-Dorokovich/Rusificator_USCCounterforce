"""Patch the I2Languages asset in the Windows Steam build of USC: Counterforce."""

import ctypes
import hashlib
import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
from bisect import bisect_left, bisect_right
from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime
from itertools import accumulate
from pathlib import Path
from ctypes import wintypes

import lz4.block
import UnityPy


ARCHIVE_NAME = "data.unity3d"
GAME_NAME = "USC Counterforce"
DATA_FOLDER = f"{GAME_NAME}_Data"
BLOCK_SIZE = 131072
Block = namedtuple("Block", "size packed mode")
Entry = namedtuple("Entry", "offset size flags name")
Bundle = namedtuple("Bundle", "header size_field block_start flags blocks entries")


class InstallationError(Exception):
    """An installation problem that can be shown in the wizard."""


def require(condition, detail):
    if not condition:
        raise InstallationError(detail)


def archive_in(folder):
    folder = Path(folder).resolve()
    data = folder if folder.name.casefold() == DATA_FOLDER.casefold() else folder / DATA_FOLDER
    info = data / "app.info"
    archive = data / ARCHIVE_NAME
    require(
        (data.parent / f"{GAME_NAME}.exe").is_file() and info.is_file()
        and info.read_text(encoding="utf-8-sig").splitlines()[:2] == ["Angry Cat Studios", GAME_NAME]
        and archive.is_file(),
        "В выбранной папке не найдена установка USC: Counterforce.",
    )
    return archive


def read_exact(file, size):
    data = file.read(size)
    require(len(data) == size, "Игровой архив обрывается раньше указанного размера.")
    return data


def read_string(file):
    data = bytearray()
    while len(data) < 1024:
        char = read_exact(file, 1)
        if char == b"\0":
            return data.decode("utf-8")
        data.extend(char)
    raise InstallationError("В игровом архиве обнаружено неподдерживаемое имя ресурса.")


def align(offset, boundary):
    return (offset + boundary - 1) & -boundary


def unpack_block(data, size, mode):
    mode &= 63
    require(mode in (0, 2, 3), "Этот способ сжатия игрового архива не поддерживается.")
    raw = lz4.block.decompress(data, uncompressed_size=size) if mode else data
    require(len(raw) == size, "Размер распакованных данных не совпал.")
    return raw


def inspect_bundle(path):
    with path.open("rb") as file:
        signature = read_string(file)
        version, = struct.unpack(">I", read_exact(file, 4))
        engine = read_string(file)
        revision = read_string(file)
        require(
            (signature, version, engine, revision) == ("UnityFS", 7, "5.x.x", "2020.3.35f1"),
            "Версия игрового архива не поддерживается этим установщиком.",
        )
        size_field = file.tell()
        size, packed, unpacked, flags = struct.unpack(">QIII", read_exact(file, 20))
        require(size == path.stat().st_size and 0 < packed <= 16 << 20 and 0 < unpacked <= 16 << 20,
                "Заголовок игрового архива повреждён.")
        require(flags & 0x40 and not flags & ~0x2FF, "Формат блоков игрового архива не поддерживается.")
        header_length = align(file.tell(), 16)
        info_start = size - packed if flags & 0x80 else header_length
        block_start = header_length if flags & 0x80 else header_length + packed
        if flags & 0x200:
            block_start = align(block_start, 16)
        file.seek(info_start)
        metadata = io.BytesIO(unpack_block(read_exact(file, packed), unpacked, flags))
        require(read_exact(metadata, 16) == bytes(16), "Формат контрольной суммы игрового архива не поддерживается.")
        block_count, = struct.unpack(">I", read_exact(metadata, 4))
        require(0 < block_count <= (unpacked - 24) // 10, "Таблица блоков повреждена.")
        blocks = [Block(*struct.unpack(">IIH", read_exact(metadata, 10))) for _ in range(block_count)]
        entry_count, = struct.unpack(">I", read_exact(metadata, 4))
        require(0 < entry_count <= 1024, "Оглавление игрового архива повреждено.")
        entries = [
            Entry(*struct.unpack(">QQI", read_exact(metadata, 20)), read_string(metadata))
            for _ in range(entry_count)
        ]
        end = info_start if flags & 0x80 else size
        require(not metadata.read(1) and block_start + sum(block.packed for block in blocks) == end,
                "Размеры блоков игрового архива не совпали.")
        require(all(0 < block.size <= BLOCK_SIZE and 0 < block.packed <= BLOCK_SIZE for block in blocks),
                "В игровом архиве обнаружен блок неподдерживаемого размера.")
        file.seek(0)
        return Bundle(read_exact(file, header_length), size_field, block_start, flags, blocks, entries)


def extract_range(path, bundle, start, length):
    require(0 <= start and 0 <= length, "Оглавление игрового архива повреждено.")
    output = io.BytesIO()
    limit = start + length
    position = 0
    with path.open("rb") as file:
        file.seek(bundle.block_start)
        for block in bundle.blocks:
            end = position + block.size
            if position >= limit:
                break
            if end > start:
                raw = unpack_block(read_exact(file, block.packed), block.size, block.mode)
                output.write(raw[max(0, start - position):min(block.size, limit - position)])
            else:
                file.seek(block.packed, os.SEEK_CUR)
            position = end
    require(output.tell() == length, "Ресурс выходит за пределы игрового архива.")
    return output.getvalue()


def read_translations(path):
    lines = path.read_bytes().decode("utf-8").split("\r\n")
    quoted = re.compile(r'^\s*\d+ string (?:Term|data) = "(.*)"$')
    rows = []
    for index, line in enumerate(lines):
        if line.strip() == "0 string Languages":
            matches = [quoted.fullmatch(lines[index + delta]) for delta in (-2, 4, 6, 8, 10, 12)]
            require(all(matches), "Встроенный файл перевода повреждён.")
            values = [match[1].replace(r"\r", "\r").replace(r"\n", "\n") for match in matches]
            rows.append((values[0], tuple(values[1:])))
    require(len(rows) == 3613 and len({key for key, _ in rows}) == len(rows),
            "Набор строк во встроенном переводе не совпадает с ожидаемым.")
    return rows


def parse_terms(data):
    def integer(offset):
        return struct.unpack_from("<i", data, offset)[0]

    def string(offset):
        size = integer(offset)
        end = offset + 4 + size
        require(size >= 0 and end <= len(data), "Строка локализации повреждена.")
        return data[offset + 4:end].decode("utf-8"), align(end, 4)

    name, offset = string(28)
    require(name == "I2Languages" and offset == 44 and integer(56) == 3613,
            "В этой версии игры не найден ожидаемый ресурс I2Languages.")
    offset = 60
    for _ in range(3613):
        term, offset = string(offset)
        require(integer(offset + 4) == 5, "Число языков в игре изменилось.")
        offset += 8
        values = []
        for slot in range(5):
            start = offset
            value, offset = string(offset)
            if slot == 2:
                span = start, offset
            values.append(value)
        require(integer(offset) == 5 and integer(offset + 12) == 0, "Структура локализации изменилась.")
        offset += 16
        yield term, tuple(values), span, offset


def patch_language_resource(previous, translations):
    stream = io.BytesIO()
    previous_offset = 0
    changed = 0
    for (key, values, span, end), (expected_key, expected_values) in zip(
        parse_terms(previous), translations, strict=True
    ):
        require(
            key == expected_key and all(values[i] == expected_values[i] for i in (0, 1, 3, 4)),
            f"Это другая версия локализации игры. Первое несовпадение — {key}.",
        )
        if values[2] != expected_values[2]:
            start, stop = span
            russian = expected_values[2].encode("utf-8")
            stream.write(previous[previous_offset:start])
            stream.write(struct.pack("<i", len(russian)))
            stream.write(russian)
            stream.write(bytes((-len(russian)) % 4))
            previous_offset = stop
            changed += 1
    require(b"Hungarian" in previous[end:], "Игра больше не использует слот magyar.")
    stream.write(previous[previous_offset:])
    current = stream.getvalue()
    require([(key, values) for key, values, _, _ in parse_terms(current)] == translations,
            "Проверка обновлённой локализации не прошла.")
    return current, changed


def patch_assets(previous, translations):
    assets = UnityPy.load(previous).file
    obj = assets.objects.get(36144)
    require(obj is not None and int(obj.type) == 114, "В архиве не найден ресурс I2Languages.")
    localized, count = patch_language_resource(obj.get_raw_data(), translations)
    if not count:
        return previous, count
    header = assets.header
    require(header.version == 22 and header.endian == "<" and header.file_size == len(previous),
            "Формат ресурсов этой версии игры не поддерживается.")
    old_end = obj.byte_start + obj.byte_size
    padding = (obj.byte_size - len(localized)) % BLOCK_SIZE
    delta = len(localized) + padding - obj.byte_size
    metadata = bytearray(previous[:header.data_offset])
    struct.pack_into(">q", metadata, 24, len(previous) + delta)
    for item in assets.objects.values():
        if item.byte_start < obj.byte_start:
            continue
        require(item.path_id == obj.path_id or item.byte_start >= old_end, "Ресурсы архива перекрываются.")
        relative = item.byte_start - header.data_offset
        record = struct.pack("<qqIi", item.path_id, relative, item.byte_size, item.type_id)
        index = metadata.find(record)
        require(index >= 48 and metadata.find(record, index + 1) < 0, "Не удалось однозначно найти запись ресурса.")
        if item.path_id == obj.path_id:
            struct.pack_into("<I", metadata, index + 16, len(localized))
        else:
            struct.pack_into("<q", metadata, index + 8, relative + delta)
    result = b"".join((metadata, previous[header.data_offset:obj.byte_start],
                       localized, bytes(padding), previous[old_end:]))
    objects = UnityPy.load(result).file.objects
    require(objects.keys() == assets.objects.keys() and objects[obj.path_id].get_raw_data() == localized,
            "Проверка содержимого игрового архива не прошла.")
    require(
        all(item.get_raw_data() == assets.objects[key].get_raw_data()
            for key, item in objects.items() if key != obj.path_id),
        "Проверка остальных ресурсов игрового архива не прошла.",
    )
    return result, count


def copy_range(source, start, stop, report_bytes, target=None):
    source.seek(start)
    checksum = hashlib.sha256()
    while start < stop:
        chunk = read_exact(source, min(8 << 20, stop - start))
        checksum.update(chunk)
        if target is not None:
            target.write(chunk)
        start += len(chunk)
        report_bytes(len(chunk))
    return checksum.digest()


def write_bundle(path, target, original, entry, replacement, report):
    logical = list(accumulate((0, *(block.size for block in original.blocks))))
    physical = list(accumulate((original.block_start, *(block.packed for block in original.blocks))))
    start, end = entry.offset, entry.offset + entry.size
    first, last = bisect_right(logical, start) - 1, bisect_left(logical, end)
    leading = extract_range(path, original, logical[first], start - logical[first])
    trailing = extract_range(path, original, end, logical[last] - end)
    body = leading + replacement + trailing
    new_blocks = []
    chunks = []
    for offset in range(0, len(body), BLOCK_SIZE):
        raw = body[offset:offset + BLOCK_SIZE]
        compressed = lz4.block.compress(raw, mode="high_compression", compression=6, store_size=False)
        if len(compressed) < len(raw):
            chunks.append(compressed)
            new_blocks.append(Block(len(raw), len(compressed), 3))
        else:
            chunks.append(raw)
            new_blocks.append(Block(len(raw), len(raw), 0))
    delta = len(replacement) - entry.size
    blocks = original.blocks[:first] + new_blocks + original.blocks[last:]
    entries = [
        item._replace(size=len(replacement)) if item == entry else
        item._replace(offset=item.offset + delta) if item.offset >= end else item
        for item in original.entries
    ]
    metadata = bytes(16) + struct.pack(">I", len(blocks))
    metadata += b"".join(struct.pack(">IIH", *block) for block in blocks)
    metadata += struct.pack(">I", len(entries))
    metadata += b"".join(struct.pack(">QQI", *item[:3]) + item.name.encode("utf-8") + b"\0" for item in entries)
    packed_info = lz4.block.compress(metadata, mode="high_compression", compression=9, store_size=False)
    # Keep the source archive's layout and alignment. The new metadata always uses LZ4HC.
    flags = (original.flags & ~63) | 3
    info_at_end = bool(flags & 0x80)
    block_start = len(original.header) + (0 if info_at_end else len(packed_info))
    if flags & 0x200:
        block_start = align(block_start, 16)
    total = block_start + sum(block.packed for block in blocks) + (len(packed_info) if info_at_end else 0)
    header = bytearray(original.header)
    struct.pack_into(">QIII", header, original.size_field, total, len(packed_info), len(metadata), flags)

    copied = 0
    copy_total = physical[first] - physical[0] + physical[-1] - physical[last]
    last_progress = 0

    def progress(count):
        nonlocal copied, last_progress
        copied += count
        value = int(copied * 32 / max(copy_total, 1))
        if value != last_progress:
            last_progress = value
            report(32 + value, "Собираем игровой архив…")

    report(32, "Собираем игровой архив…")
    with path.open("rb") as source, target.open("wb") as output:
        output.write(header)
        if not info_at_end:
            output.write(packed_info)
        output.write(bytes(block_start - output.tell()))
        prefix_hash = copy_range(source, physical[0], physical[first], progress, output)
        for chunk in chunks:
            output.write(chunk)
        suffix_start = output.tell()
        suffix_hash = copy_range(source, physical[last], physical[-1], progress, output)
        suffix_end = output.tell()
        if info_at_end:
            output.write(packed_info)
        require(output.tell() == total, "Размер нового игрового архива не совпал.")
        output.flush()
        os.fsync(output.fileno())

    report(68, "Проверяем установленный перевод и остальные ресурсы…")
    staged = inspect_bundle(target)
    require(staged.entries == entries and staged.blocks == blocks, "Не прошла проверка оглавления архива.")
    require(extract_range(target, staged, logical[first], len(body)) == body,
            "Проверка содержимого нового архива не прошла.")
    verified = 0

    def verify_progress(count):
        nonlocal verified
        verified += count
        if verified % (128 << 20) < count:
            report(70 + int(27 * verified / max(copy_total, 1)), "Проверяем целостность архива…")

    with target.open("rb") as file:
        require(copy_range(file, block_start, block_start + physical[first] - physical[0], verify_progress) == prefix_hash
                and copy_range(file, suffix_start, suffix_end, verify_progress) == suffix_hash,
                "Проверка неизменённых блоков игрового архива не прошла.")


def require_game_closed():
    result = subprocess.run(
        ["tasklist.exe", "/FI", f"IMAGENAME eq {GAME_NAME}.exe", "/FO", "CSV", "/NH"],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True,
    )
    require(f'"{GAME_NAME}.exe"'.lower().encode() not in result.stdout.lower(),
            "Закройте USC: Counterforce перед установкой.")


@contextmanager
def installation_lock(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    path_hash = hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()
    handle = kernel.CreateMutexW(None, False, f"Local\\USC-Russian-Setup-{path_hash}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
    try:
        require(acquired, "Для этой папки уже запущена установка.")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def replace_archive(source, target, backup):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)
    replace.restype = wintypes.BOOL
    if not replace(str(target), str(source), str(backup) if backup else None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def install(folder, dump, make_backup, report):
    path = archive_in(folder)
    with installation_lock(path):
        require_game_closed()
        original_stat = path.stat()
        report(5, "Проверяем версию игры и таблицу локализации…")
        bundle = inspect_bundle(path)
        candidates = [item for item in bundle.entries if item.name == "resources.assets"]
        require(len(candidates) == 1, "В игровом архиве не найден файл resources.assets.")
        entry = candidates[0]
        require(0 < entry.size <= 256 << 20, "Размер ресурсов этой версии игры не поддерживается.")
        require(all(item == entry or item.offset + item.size <= entry.offset
                    or item.offset >= entry.offset + entry.size for item in bundle.entries),
                "Ресурсы в игровом архиве перекрываются.")
        current = extract_range(path, bundle, entry.offset, entry.size)
        translations = read_translations(dump)
        updated, changed = patch_assets(current, translations)
        if not changed and not make_backup:
            report(100, "Этот перевод уже установлен.")
            return changed, None
        require(shutil.disk_usage(path.parent).free >= original_stat.st_size + (64 << 20),
                "Недостаточно места на диске. Для установки требуется около 3,6 ГБ свободного места.")
        backup = None
        with tempfile.NamedTemporaryFile(prefix=".usc-setup-", suffix=".tmp", dir=path.parent, delete=False) as file:
            staged = Path(file.name)
        try:
            if not changed:
                report(35, "Копируем текущий игровой архив…")
                shutil.copyfile(path, staged)
            else:
                write_bundle(path, staged, bundle, entry, updated, report)
            report(98, "Устанавливаем проверенный архив…")
            require_game_closed()
            stat = path.stat()
            require((stat.st_size, stat.st_mtime_ns, stat.st_ino) ==
                    (original_stat.st_size, original_stat.st_mtime_ns, original_stat.st_ino),
                    "Игровой архив изменился во время установки. Запустите установку заново.")
            if make_backup:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup = path.with_name(f"{ARCHIVE_NAME}.backup-{timestamp}.bak")
                require(not backup.exists(), "Файл резервной копии уже существует.")
            if changed:
                replace_archive(staged, path, backup)
            else:
                staged.rename(backup)
        finally:
            staged.unlink(missing_ok=True)
        report(100, "Русификатор установлен.")
        return changed, backup
