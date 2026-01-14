import serial
import time
import threading
import Printer


class ZigbeeSerial():
    def __init__(self, port='/dev/ttyUSB0', baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.peak_log = []
        self.port_lock = threading.Lock()
        self._threshold_buffer = ""   # ← добавляем буфер для порога


    def init_serial(self):
        """
        Инициализация Zigbee серийного порта

        Returns:
            True если успешно, False если ошибка
        """
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            print(f"[Zigbee] ✓ Port {self.port} opened at {self.baudrate} baud")
            time.sleep(0.5)  # Даём устройству инициализироваться
            return True
        except serial.SerialException as e:
            print(f"[Zigbee] ERROR: Failed to open port - {e}")
            self.ser = None
            return False

    def send_command(self, command):
        """
        Безопасная отправка команды через Zigbee

        Args:
            command: строка команды (будет добавлен \r\n если нет)

        Returns:
            True если успешно, False если ошибка
        """
        if self.ser is None or not self.ser.is_open:
            print("[Zigbee] ERROR: Port not initialized or closed!")
            return False

        try:
            with self.port_lock:
                # Формируем команду с переводом строки
                if not command.endswith('\r\n'):
                    command += '\r\n'

                # Отправляем данные
                command_bytes = command.encode('ascii', errors='replace')
                self.ser.write(command_bytes)
                self.ser.flush()

                print(f"[Zigbee Sent] {command.strip()}")
                print(Printer.DELIMETER)

                # Пытаемся получить ответ (опционально)
                time.sleep(0.2)
                if self.ser.in_waiting > 0:
                    response = self.ser.readline().decode('ascii', errors='replace').strip()
                    if response:
                        print(f"[Zigbee Response] {response}")

                return True

        except Exception as e:
            print(f"[Zigbee] ERROR sending command: {e}")
            return False

    def send_data(self, data):
        """
        Отправка бинарных данных через Zigbee

        Args:
            data: bytes для отправки

        Returns:
            True если успешно, False если ошибка
        """
        if self.ser is None or not self.ser.is_open:
            print("[Zigbee] ERROR: Port not open!")
            return False

        try:
            with self.port_lock:
                self.ser.write(data)
                self.ser.flush()
                return True
        except Exception as e:
            print(f"[Zigbee] ERROR sending data: {e}")
            return False

    def read_data(self, size=1024):
        """
        Чтение данных из Zigbee (неблокирующее)

        Args:
            size: максимум байт для чтения

        Returns:
            bytes прочитанные данные или пустые bytes
        """
        if self.ser is None or not self.ser.is_open:
            return b''

        try:
            if self.ser.in_waiting > 0:
                return self.ser.read(min(size, self.ser.in_waiting))
            return b''
        except Exception as e:
            print(f"[Zigbee] ERROR reading data: {e}")
            return b''

    def read_line(self):
        """
        Чтение одной строки из Zigbee (до \n)

        Returns:
            str прочитанная строка или пустая строка
        """
        if self.ser is None or not self.ser.is_open:
            return ''

        try:
            line = self.ser.readline()
            if line:
                return line.decode('ascii', errors='replace').strip()
            return ''
        except Exception as e:
            print(f"[Zigbee] ERROR reading line: {e}")
            return ''

    def close_serial(self):
        """
        Безопасное закрытие Zigbee порта
        """
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
                print("[Zigbee] ✓ Port closed")
            except Exception as e:
                print(f"[Zigbee] ERROR closing port: {e}")

    def is_connected(self):
        """
        Проверка подключения

        Returns:
            True если порт открыт, False иначе
        """
        return self.ser is not None and self.ser.is_open

    def clear_peak_log(self):
        """
        Очистить логирование пиков
        """
        self.peak_log.clear()
        print("[Zigbee] Peak log cleared")

    def add_peak_record(self, record):
        """
        Добавить запись о пике в лог

        Args:
            record: dict с информацией о пике
        """
        self.peak_log.append(record)

    def __del__(self):
        """
        Деструктор: закрываем порт при удалении объекта
        """
        try:
            self.close_serial()
        except:
            pass

    def check_incoming_threshold(self):
        """
        МГНОВЕННАЯ проверка: ищет команду SET:char в буфере.
        Не блокирует поток. Возвращает int (новый порог) или None.
        """
        if self.ser is None or not self.ser.is_open:
            return None

        try:
            # 1. Проверка без блокировки: есть ли байты?
            if self.ser.in_waiting > 0:
                # Читаем всё, что накопилось
                incoming = self.ser.read(self.ser.in_waiting)

                # Дописываем в хвост внутреннего буфера (чтобы не разорвать команду)
                text = incoming.decode('ascii', errors='ignore')
                self._threshold_buffer += text

                # 2. Ищем паттерн "SET:" + одна буква от 'a' до 't'
                # Ищем ПОСЛЕДНЕЕ вхождение (если пришло сразу 10 команд, берем последнюю)
                import re
                matches = re.findall(r'SET:([a-t])', self._threshold_buffer)

                if matches:
                    last_char = matches[-1]  # Берем последнюю актуальную букву

                    # 3. ДЕКОДИРУЕМ: 'a' -> 1 -> 10 000 000
                    multiplier = ord(last_char) - ord('a') + 1
                    new_threshold = multiplier * 10000000

                    print(f"[Zigbee] DECODER: Char '{last_char}' -> Threshold {new_threshold}")

                    # Очищаем буфер, команда выполнена
                    self._threshold_buffer = ""
                    return new_threshold

                # Защита от переполнения памяти, если мусор копится
                if len(self._threshold_buffer) > 50:
                    self._threshold_buffer = self._threshold_buffer[-20:]

        except Exception:
            pass  # Игнорируем ошибки чтения, чтобы не сломать основной поток

        return None


