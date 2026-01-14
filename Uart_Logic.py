import serial
import sys
import select
import time
import numpy as np
import ByInConvert
from collections import deque
from datetime import datetime

# ============================================================================
# ДИНАМИЧЕСКИЙ ПОРОГ (обновляется по Zigbee)
# ============================================================================
PEAK_THRESHOLD_FROM_PC = 150000000


# ════════════════════════════════════════════════════════════════════════════════
# ВАЛИДАЦИЯ ПАКЕТОВ (быстрая версия для RPi)
# ════════════════════════════════════════════════════════════════════════════════
def is_packet_valid_lite(data):
    """
    Симметричная проверка валидности по модулю.
    Работает одинаково для положительных и отрицательных пиков.
    """
    if len(data) == 0:
        return False

    # 1. Ищем максимальную амплитуду по модулю (без создания новых списков для скорости)
    max_abs = 0
    for val in data:
        # Аналог abs(val), но быстрее внутри цикла
        v = val if val >= 0 else -val
        if v > max_abs:
            max_abs = v

    # Проверка 1: Слишком тихо (шум)
    if max_abs < 1000:
        # print(f"[VALID] REJECT: too quiet ({max_abs})")
        return False

    # Проверка 2: Нереально громко (глюк АЦП)
    if max_abs > 4000000000:
        # print(f"[VALID] REJECT: too loud ({max_abs})")
        return False

    # Проверка 3: Активность сигнала
    # Сигнал не должен быть одним случайным пиком ("иглой").
    # Хотя бы 2% точек должны быть громче 20% от максимума.
    threshold = max_abs * 0.2
    count_active = 0
    min_active_points = len(data) * 0.02

    for val in data:
        v = val if val >= 0 else -val
        if v > threshold:
            count_active += 1
            # Оптимизация: как только набрали нужное кол-во, сразу одобряем
            if count_active > min_active_points:
                return True

    # print(f"[VALID] REJECT: active points too low ({count_active})")
    return False



class Serial_reader:
    """
    Класс для чтения данных с UART (от АЦП), детектирования звуковых пиков
    и отправки сжатых событий через Zigbee.
    """

    # Маркеры пакета от АЦП
    START_MARKER = b"\xB6" * 10
    END_MARKER = b"\x49" * 10

    def __init__(
            self,
            baud_rate=256000,
            serial_port="/dev/serial0",
            main_total_packets=1,
            main_packet_info=None,
            main_runflag=False,
            main_last_packet_peak_detected=False,
            main_ser=None,
            main_ring_que=None,
    ):
        self.baud_rate = baud_rate
        self.serial_port = serial_port
        self.main_ser = main_ser
        self.main_ring_que = main_ring_que if main_ring_que is not None else deque(maxlen=10)
        self.main_total_packets = main_total_packets
        self.main_packet_info = main_packet_info if main_packet_info is not None else []
        self.main_run_flag = main_runflag
        self.main_last_packet_peak_detected = main_last_packet_peak_detected
        self.buffer = bytearray()

    def detect_multiple_peaks(self, data, peak_threshold=None, min_gap_between_events=1000):
        """
        Детектирование отдельных звуковых событий.
        """
        global PEAK_THRESHOLD_FROM_PC
        if peak_threshold is None:
            peak_threshold = PEAK_THRESHOLD_FROM_PC

        abs_data = np.abs(data)
        above_threshold = abs_data > peak_threshold

        if not np.any(above_threshold):
            return []

        transitions = np.diff(above_threshold.astype(int))
        event_starts = np.where(transitions == 1)[0]
        event_ends = np.where(transitions == -1)[0]

        if len(event_starts) == 0 and len(event_ends) == 0:
            return [(0, len(data) - 1)]

        if len(event_starts) > 0 and len(event_ends) == 0:
            return [(int(event_starts[0]), len(data) - 1)]

        if len(event_starts) == 0 and len(event_ends) > 0:
            return [(0, int(event_ends[-1]))]

        events = []
        if len(event_starts) > 0 and len(event_ends) > 0:
            if event_starts[0] < event_ends[0]:
                for i, start in enumerate(event_starts):
                    start = int(start)
                    end = int(event_ends[i]) if i < len(event_ends) else len(data) - 1
                    events.append((start, end))
            else:
                events.append((0, int(event_ends[0])))
                for i in range(1, len(event_starts)):
                    start = int(event_starts[i])
                    end = int(event_ends[i]) if i < len(event_ends) else len(data) - 1
                    events.append((start, end))

        if len(events) <= 1:
            return events

        final_events = [events[0]]
        for i in range(1, len(events)):
            curr_start, curr_end = events[i]
            last_start, last_end = final_events[-1]

            if (curr_start - last_end) < min_gap_between_events:
                final_events[-1] = (last_start, curr_end)
            else:
                final_events.append((curr_start, curr_end))

        return final_events

    def send_packet_via_zigbee(
            self, zigbee_serial, packet_data, packet_num, event_start=None, event_end=None
    ):
        import struct

        # ← БЕЗ проверки валидации (пакет уже валидирован раньше)

        if event_start is None or event_end is None:
            return False

        window_left = 300
        window_right = 300
        data_start = max(0, int(event_start) - window_left)
        data_end = min(len(packet_data), int(event_end) + window_right)
        window_data = packet_data[data_start:data_end]

        if len(window_data) == 0:
            return False

        compression = 4
        compressed = window_data[::compression]
        arr = np.array(compressed, dtype=np.int32)
        payload = arr.tobytes()

        header = b"PKT" + struct.pack(
            ">IIHH", int(packet_num), int(data_start), compression, len(arr)
        )

        frame = header + payload

        try:
            if zigbee_serial.ser and zigbee_serial.ser.is_open:
                time.sleep(0.01)
                zigbee_serial.ser.write(b"\r")
                zigbee_serial.ser.write(frame)
                zigbee_serial.ser.flush()
                print(f"[Zigbee] Bin sent: Pack#{packet_num} ({len(frame)} bytes)")
                return True
            else:
                return False
        except Exception as e:
            print(f"[Zigbee ERROR] {e}")
            return False

    def main_serial_reader(self, zigbee_serial, peak_treshold, stop_byte):
        try:
            print("[UART] main_serial_reader started")

            # Определяем ОС для корректной работы ввода (опционально)
            try:
                import platform
                is_windows = platform.system() == 'Windows'
            except:
                is_windows = False

            while self.main_run_flag:

                # -------------------------------------------------------------
                # 1. ПРОВЕРКА ОБНОВЛЕНИЯ ПОРОГА ЧЕРЕЗ ZIGBEE (НОВОЕ)
                # -------------------------------------------------------------
                # Этот блок работает быстро и не блокирует поток
                if zigbee_serial is not None:
                    # Спрашиваем: "Пришла ли команда SET:x?"
                    new_val = zigbee_serial.check_incoming_threshold()

                    if new_val is not None:
                        peak_treshold = new_val
                        print(f"\n[UART] === THRESHOLD UPDATED: {peak_treshold} ===\n")
                # -------------------------------------------------------------

                # Обработка выхода по Enter (для Linux/консоли)
                # if not is_windows:
                #     try:
                #         if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                #             line = sys.stdin.readline()
                #             if line.strip() == "":
                #                 print("\n[User] ENTER pressed - stopping...")
                #                 self.main_run_flag = False
                #                 break
                #     except:
                #         pass

                if self.main_ser is None or not self.main_ser.is_open:
                    print("[ERROR] Serial port is not initialized!")
                    self.main_run_flag = False
                    break

                # 2. ЧТЕНИЕ ДАННЫХ С МИКРОФОНА
                n = self.main_ser.in_waiting
                if n > 0:
                    try:
                        data = self.main_ser.read(n)
                        self.buffer.extend(data)
                    except Exception as e:
                        print(f"[ERROR] Failed to read: {e}")
                        time.sleep(0.01)
                        continue
                else:
                    # Если данных нет, спим чуть-чуть, чтобы не грузить ЦП
                    time.sleep(0.002)
                    continue

                # 3. ПОИСК И ОБРАБОТКА ПАКЕТОВ
                idx_end = self.buffer.find(self.END_MARKER)
                if idx_end != -1:
                    packet_candidate = self.buffer[:idx_end]
                    idx_start = packet_candidate.rfind(self.START_MARKER)

                    if idx_start != -1:
                        # Пакет найден корректно
                        end_pos = idx_end + len(self.END_MARKER)
                        package = self.buffer[idx_start:end_pos]

                        # print(f"[Pck #{self.main_total_packets}] - [Sz={len(package)}]")

                        # Удаляем обработанное из буфера
                        self.buffer = self.buffer[end_pos:]

                        # Проверка размера пакета (грубая)
                        if len(package) > 19000:
                            try:
                                converted_Pck = ByInConvert.bytesIntsConvert(package)
                            except Exception as e:
                                print(f"[ERROR] Failed to convert package: {e}")
                                continue

                            if not converted_Pck:
                                print("[WARNING] Conversion returned empty package")
                                continue

                            # Сохраняем в кольцевой буфер (для истории/дебага)
                            self.main_ring_que.append(converted_Pck)
                            self.main_total_packets += 1

                            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-4]

                            packet_info = {
                                "buffer_index": len(self.main_ring_que),
                                "global_number": self.main_total_packets,
                                "timestamp": timestamp,
                                "packet_size": len(package),
                            }
                            self.main_packet_info.append(packet_info)

                            current_packet = np.array(converted_Pck, dtype=np.int64)

                            # === УДАЛЕНИЕ СМЕЩЕНИЯ ===
                            # Вычисляем среднее значение (уровень тишины) и вычитаем его

                            #dc_offset = np.mean(current_packet)
                            #current_packet = current_packet - dc_offset

                            if len(current_packet) > 0:
                                current_packet -= current_packet[0]

                            # 4. ДЕТЕКЦИЯ СОБЫТИЙ (Используем актуальный peak_treshold!)
                            events_list = self.detect_multiple_peaks(
                                current_packet, peak_treshold, min_gap_between_events=1000
                            )

                            if len(events_list) > 0:
                                self.main_last_packet_peak_detected = True
                                print(
                                    f"[Packet #{self.main_total_packets}] {timestamp} - "
                                    f"Detected {len(events_list)} event(s) (Thr={peak_treshold})"
                                )

                                for event_num, (event_start, event_end) in enumerate(events_list, 1):
                                    event_start = int(event_start)
                                    event_end = int(event_end)

                                    event_data = current_packet[event_start:event_end + 1]
                                    if event_data.size == 0:
                                        continue

                                    # ВАЛИДАЦИЯ (Lite)
                                    window_data_check = current_packet[
                                        max(0, event_start - 300):min(len(current_packet), event_end + 300)
                                    ]

                                    if not is_packet_valid_lite(window_data_check.tolist()):
                                        print(
                                            f"[WARNING] Event {event_num} in Pack#{self.main_total_packets} SKIPPED (invalid)")
                                        continue

                                    event_max_abs = float(np.max(np.abs(event_data)))
                                    event_duration = event_end - event_start + 1

                                    # Логирование пика (для меню)
                                    peak_record = {
                                        "time": timestamp,
                                        "packet_num": self.main_total_packets,
                                        "event_num": event_num,
                                        "total_events_in_packet": len(events_list),
                                        "event_start_idx": event_start,
                                        "event_end_idx": event_end,
                                        "max_value": event_max_abs,
                                        "duration": event_duration,
                                    }
                                    if hasattr(zigbee_serial, "peak_log"):
                                        zigbee_serial.peak_log.append(peak_record)

                                    # 5. ОТПРАВКА БИНАРНИКА (Zigbee)
                                    self.send_packet_via_zigbee(
                                        zigbee_serial,
                                        current_packet.tolist(),
                                        self.main_total_packets,
                                        event_start=event_start,
                                        event_end=event_end,
                                    )

                                    # 6. ОТПРАВКА ТЕКСТА (Zigbee)
                                    SCALE = 2 ** 31
                                    loud_value = event_max_abs / SCALE

                                    message = (
                                        f"{timestamp} | "
                                        f"Pack #{self.main_total_packets} | "
                                        f"Event {event_num}/{len(events_list)} | "
                                        f"Loud={loud_value:.4f}"
                                    )

                                    try:
                                        zigbee_serial.send_command(message)
                                    except Exception as e:
                                        print(f"[WARNING] Failed to send text via Zigbee: {e}")

                                    print(
                                        f"   └─ Event {event_num}: Start={event_start}, End={event_end}, "
                                        f"Max={event_max_abs:.0f}"
                                    )
                        else:
                            print(f"[WARNING] Packet too small: {len(package)} bytes")

                    else:
                        # Маркер конца найден, а начала нет -> мусор
                        # print(f"[DROP] Garbage detected (no START before END at {idx_end})")
                        self.buffer = self.buffer[idx_end + len(self.END_MARKER):]

                # Короткая пауза в цикле обработки
                # time.sleep(0.001)

        except Exception as e:
            print(f"\n[ERROR] Error in serial reader: {e}")
            import traceback
            traceback.print_exc()
            self.main_run_flag = False

        finally:
            print("\n[UART] main_serial_reader exiting...")
            if self.main_ser and self.main_ser.is_open:
                try:
                    self.main_ser.write(stop_byte)
                    self.main_ser.flush()
                    print(f"{datetime.now().strftime('%H:%M:%S')} - Stop byte sent.")
                except Exception as e:
                    print(f"[ERROR] Failed to send stop byte: {e}")

