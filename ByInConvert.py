import numpy as np


def bytesIntsConvert(data):
    """
    Конвертирует байты в массив signed int32 (Big-Endian).
    Входит: полный пакет (с маркерами START и END).
    Выходит: массив int32 (маркеры уже удалены).
    """
    if not data:
        return []

    # ✅ РАБОЧАЯ ВЕРСИЯ:
    START_MARKER = b'\xB6' * 10  # 10 байт начало
    END_MARKER = b'\x49' * 10  # 10 байт конец

    # ТОЧНЫЙ поиск границ
    if not data.startswith(START_MARKER):
        print(f"[ERROR] Packet doesn't start with START_MARKER!")
        return []

    if not data.endswith(END_MARKER):
        print(f"[ERROR] Packet doesn't end with END_MARKER!")
        return []

    # Вырезаем РОВНО маркеры
    payload = data[len(START_MARKER):-len(END_MARKER)]

    if not payload or len(payload) == 0:
        return []

    # ЗАЩИТА: Проверяем кратность 4
    remainder = len(payload) % 4
    if remainder != 0:
        print(f"[ByInConvert WARNING] Payload len {len(payload)} not multiple of 4. Trimming {remainder} bytes.")
        payload = payload[:-remainder]

    if len(payload) == 0:
        return []

    # Конвертируем: Big-Endian signed int32
    int_array = []
    for i in range(0, len(payload), 4):
        four_bytes = payload[i:i + 4]
        # byteorder='big' (Big-Endian), signed=True (signed int)
        value = int.from_bytes(four_bytes, byteorder='big', signed=True)
        int_array.append(value)

    return int_array
