import sys
import time
import struct
import threading
import queue
import numpy as np
from datetime import datetime
import serial
from serial.tools import list_ports

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QComboBox,
                             QGroupBox, QTreeWidget, QTreeWidgetItem,
                             QMessageBox, QSplitter)
from PyQt6.QtCore import QTimer, pyqtSignal, QObject, Qt
from PyQt6.QtGui import QFont, QColor, QBrush

import pyqtgraph as pg

# ============================================================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ
# ============================================================================
BAUDRATES = [4800, 9600, 19200, 38400, 57600, 115200, 256000, 460800]

# Настройка стиля графиков (белый фон, черные оси)
pg.setConfigOption('background', 'w')
pg.setConfigOption('foreground', 'k')
pg.setConfigOptions(antialias=True)


# ============================================================================
# ЛОГИКА UART В ОТДЕЛЬНОМ ПОТОКЕ (КОПИЯ ЛОГИКИ ИЗ ТВОЕГО TKINTER)
# ============================================================================
class UartWorker(QObject):
    """
    Воркер работает в отдельном потоке.
    Логика чтения 1-в-1 повторяет твой Tkinter скрипт (read_uart_thread).
    """
    sig_packet_received = pyqtSignal(int, object, int)  # packet_num, data(np.array), offset
    sig_log_message = pyqtSignal(str)  # текстовое сообщение
    sig_threshold_update = pyqtSignal(int)  # обновление порога
    sig_status_update = pyqtSignal(str)  # статус
    sig_connection_error = pyqtSignal(str)  # ошибка

    def __init__(self):
        super().__init__()
        self.ser = None
        self.is_running = False
        self.command_queue = queue.Queue(maxsize=20)
        self.last_threshold_send_time = 0
        self.threshold_send_cooldown = 0.3

    def connect_port(self, port_name, baudrate):
        try:
            self.ser = serial.Serial(port_name, baudrate, timeout=0.1)
            time.sleep(0.5)
            self.ser.reset_input_buffer()
            self.is_running = True
            self.sig_status_update.emit(f"▶ Приём на {port_name}, {baudrate} baud")
            self.read_loop()
        except Exception as e:
            self.sig_connection_error.emit(str(e))

    def stop(self):
        self.is_running = False
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except:
                pass
        self.sig_status_update.emit("⏹ Приём остановлен")

    def send_command(self, cmd_str):
        """Добавить команду в очередь (как в Tkinter скрипте)"""
        try:
            self.command_queue.put_nowait(cmd_str)
        except:
            pass

    def read_loop(self):
        print("[DEBUG] UART loop started")
        buffer = bytearray()

        while self.is_running and self.ser and self.ser.is_open:
            # -----------------------------------------------------------
            # 1. ОТПРАВКА КОМАНД (из process_commands в Tkinter)
            # -----------------------------------------------------------
            try:
                if not self.command_queue.empty():
                    now = time.time()
                    if now - self.last_threshold_send_time >= self.threshold_send_cooldown:
                        # Берем команду, кодируем и шлем
                        cmd = self.command_queue.get_nowait()
                        # В твоем скрипте cmd уже строка "SET:a\r\n", кодируем в ascii
                        if isinstance(cmd, str):
                            cmd_bytes = cmd.encode('ascii', errors='ignore')
                        else:
                            cmd_bytes = cmd

                        self.ser.write(cmd_bytes)
                        self.ser.flush()
                        self.last_threshold_send_time = now
                        print(f"[DEBUG] Sent: {cmd.strip()}")
            except Exception as e:
                print(f"[ERROR] Write error: {e}")

            # -----------------------------------------------------------
            # 2. ЧТЕНИЕ ДАННЫХ (из read_uart_thread в Tkinter)
            # -----------------------------------------------------------
            try:
                n = self.ser.in_waiting
                if n > 0:
                    data = self.ser.read(n)
                    buffer.extend(data)

                while len(buffer) > 0:
                    idx_pkt = buffer.find(b'PKT')
                    idx_n = buffer.find(b'\n')
                    idx_r = buffer.find(b'\r')

                    idx_newline = -1
                    if idx_n != -1 and idx_r != -1:
                        idx_newline = min(idx_n, idx_r)
                    elif idx_n != -1:
                        idx_newline = idx_n
                    elif idx_r != -1:
                        idx_newline = idx_r

                    # ПРИОРИТЕТ 1: Бинарный пакет (PKT)
                    if idx_pkt != -1 and (idx_newline == -1 or idx_pkt < idx_newline):
                        if idx_pkt > 0:
                            buffer = buffer[idx_pkt:]  # отбрасываем мусор до PKT

                        if len(buffer) >= 15:
                            try:
                                packet_num, offset, compression, length = struct.unpack('>IIHH', buffer[3:15])
                                total_size = 15 + length * 4

                                # Защита от мусора (как в Tkinter)
                                if total_size <= 15 or total_size > 200000:
                                    buffer = buffer[1:]
                                    continue

                                if len(buffer) >= total_size:
                                    # Полный пакет собран
                                    data_bytes = buffer[15:total_size]
                                    data_compressed = np.frombuffer(data_bytes, dtype=np.int32)

                                    if compression > 1:
                                        data = np.repeat(data_compressed, compression)
                                    else:
                                        data = data_compressed

                                    self.sig_packet_received.emit(packet_num, data, offset)
                                    buffer = buffer[total_size:]
                                else:
                                    break  # Ждем данные
                            except struct.error:
                                buffer = buffer[1:]
                        else:
                            break

                            # ПРИОРИТЕТ 2: Текстовая строка
                    elif idx_newline != -1:
                        line_bytes = buffer[:idx_newline].strip()

                        skip = 1
                        if idx_newline < len(buffer) - 1 and buffer[idx_newline:idx_newline + 2] in (b'\r\n', b'\n\r'):
                            skip = 2
                        buffer = buffer[idx_newline + skip:]

                        if line_bytes:
                            try:
                                line_str = line_bytes.decode('ascii', errors='replace').strip()

                                # Фильтры (из твоего кода)
                                if '\ufffd' in line_str: continue
                                if len(line_str) < 2: continue
                                if len(line_str) > 5:
                                    alnum_count = sum(c.isalnum() for c in line_str)
                                    if alnum_count < len(line_str) * 0.3: continue

                                # Проверка на THRESHOLD=...
                                if line_str.startswith('THRESHOLD='):
                                    try:
                                        val = int(line_str.split('=')[1])
                                        self.sig_threshold_update.emit(val)
                                    except:
                                        pass
                                else:
                                    self.sig_log_message.emit(line_str)
                            except:
                                pass
                    else:
                        break  # Ждем

                time.sleep(0.005)  # Как в Tkinter

            except Exception as e:
                print(f"[ERROR] Read loop: {e}")
                self.sig_connection_error.emit(str(e))
                break

        print("[DEBUG] UART loop finished")


# ============================================================================
# ГЛАВНОЕ ОКНО
# ============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UART Tool - Sound Analysis (PyQt6)")
        self.resize(1600, 800)

        # Данные
        self.packets_storage = {}
        self.events_storage = {}
        self.pending_events = {}
        self.pending_timeout = 5.0

        self.current_threshold = 150000000

        # --- UI LAYOUT ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 1. ЛЕВАЯ ПАНЕЛЬ (Таблица событий)
        # ===============================================
        self.tree = QTreeWidget()
        # Заголовки как в твоем Tkinter
        self.tree.setHeaderLabels(["Time RPi", "Time PC", "Event Info", "Thr"])
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 220)
        self.tree.setColumnWidth(3, 50)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemClicked.connect(self.on_tree_click)
        self.tree.setFont(QFont("Segoe UI", 9))

        left_layout = QVBoxLayout()
        left_lbl = QLabel("События")
        left_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(left_lbl)
        left_layout.addWidget(self.tree)

        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        left_widget.setFixedWidth(480)

        # 2. ЦЕНТРАЛЬНАЯ ПАНЕЛЬ (График)
        # ===============================================
        center_layout = QVBoxLayout()
        center_lbl = QLabel("График")
        center_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        center_layout.addWidget(center_lbl)

        # Виджет графика (PyQtGraph)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('left', 'Амплитуда')
        self.plot_widget.setLabel('bottom', 'Индекс сэмпла')

        # Линии порога (красные пунктирные)
        self.line_thr_pos = pg.InfiniteLine(angle=0, pen=pg.mkPen('r', width=1, style=Qt.PenStyle.DashLine))
        self.line_thr_neg = pg.InfiniteLine(angle=0, pen=pg.mkPen('r', width=1, style=Qt.PenStyle.DashLine))
        self.plot_widget.addItem(self.line_thr_pos)
        self.plot_widget.addItem(self.line_thr_neg)

        # Линия данных (синяя)
        self.plot_data_item = self.plot_widget.plot([], pen=pg.mkPen('#1f77b4', width=1.5))

        center_layout.addWidget(self.plot_widget)

        self.stats_label = QLabel("...")
        self.stats_label.setStyleSheet("background: #f0f0f0; padding: 4px; border: 1px solid #ccc;")
        center_layout.addWidget(self.stats_label)

        center_widget = QWidget()
        center_widget.setLayout(center_layout)

        # 3. ПРАВАЯ ПАНЕЛЬ (Управление)
        # ===============================================
        right_layout = QVBoxLayout()
        right_lbl = QLabel("Управление")
        right_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(right_lbl)

        # Подключение
        grp_conn = QGroupBox("Подключение")
        grp_conn_layout = QVBoxLayout()
        self.combo_ports = QComboBox()
        btn_refresh = QPushButton("Обновить порты")
        btn_refresh.clicked.connect(self.refresh_ports)

        grp_conn_layout.addWidget(QLabel("Порт:"))
        grp_conn_layout.addWidget(self.combo_ports)
        grp_conn_layout.addWidget(btn_refresh)

        grp_conn_layout.addWidget(QLabel("Baudrate:"))
        self.combo_baud = QComboBox()
        self.combo_baud.addItems([str(b) for b in BAUDRATES])
        self.combo_baud.setCurrentText("256000")
        grp_conn_layout.addWidget(self.combo_baud)
        grp_conn.setLayout(grp_conn_layout)
        right_layout.addWidget(grp_conn)

        # Порог
        grp_thr = QGroupBox("Порог (Threshold)")
        grp_thr_layout = QVBoxLayout()
        hbox_thr = QHBoxLayout()

        self.combo_thr = QComboBox()
        # 1..20
        self.combo_thr.addItems([str(i) for i in range(1, 21)])
        self.combo_thr.setCurrentText("15")

        btn_set_thr = QPushButton("Отправить")
        btn_set_thr.setStyleSheet("background-color: #90EE90;")
        btn_set_thr.clicked.connect(self.send_threshold_cmd)

        hbox_thr.addWidget(self.combo_thr)
        hbox_thr.addWidget(btn_set_thr)
        grp_thr_layout.addLayout(hbox_thr)

        self.lbl_thr_val = QLabel(f"Текущий: {self.current_threshold}")
        grp_thr_layout.addWidget(self.lbl_thr_val)
        grp_thr.setLayout(grp_thr_layout)
        right_layout.addWidget(grp_thr)

        # Кнопки
        self.btn_start = QPushButton("▶ НАЧАТЬ")
        self.btn_start.setStyleSheet("background-color: #90EE90; font-weight: bold; padding: 6px;")
        self.btn_start.clicked.connect(self.start_reading)

        self.btn_stop = QPushButton("⏹ СТОП")
        self.btn_stop.setStyleSheet("background-color: #ffb3b3; font-weight: bold; padding: 6px;")
        self.btn_stop.clicked.connect(self.stop_reading)
        self.btn_stop.setEnabled(False)

        btn_clear = QPushButton("🗑 Очистить список")
        btn_clear.clicked.connect(self.clear_all)

        btn_exit = QPushButton("❌ Выход")
        btn_exit.clicked.connect(self.close)

        right_layout.addWidget(self.btn_start)
        right_layout.addWidget(self.btn_stop)
        right_layout.addSpacing(10)
        right_layout.addWidget(btn_clear)
        right_layout.addStretch()
        right_layout.addWidget(btn_exit)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        right_widget.setFixedWidth(200)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Готов к работе")

        # --- WORKER ---
        self.worker = UartWorker()

        self.tmr_check = QTimer()
        self.tmr_check.timeout.connect(self.check_pending_events)
        self.tmr_check.start(1000)

        self.refresh_ports()
        self.update_plot_threshold_lines()

    def refresh_ports(self):
        self.combo_ports.clear()
        ports = [p.device for p in list_ports.comports()]
        self.combo_ports.addItems(ports)

    def start_reading(self):
        port = self.combo_ports.currentText()
        if not port:
            QMessageBox.warning(self, "Ошибка", "Выберите порт!")
            return
        baud = int(self.combo_baud.currentText())

        # Подключаем сигналы
        try:
            self.worker.sig_packet_received.connect(self.on_packet_received)
            self.worker.sig_log_message.connect(self.on_log_message)
            self.worker.sig_threshold_update.connect(self.on_threshold_update_from_uart)
            self.worker.sig_status_update.connect(self.status_bar.showMessage)
            self.worker.sig_connection_error.connect(self.on_connection_error)

            t = threading.Thread(target=self.worker.connect_port, args=(port, baud), daemon=True)
            t.start()

            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.combo_ports.setEnabled(False)
            self.combo_baud.setEnabled(False)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def stop_reading(self):
        self.worker.stop()
        try:
            self.worker.sig_packet_received.disconnect()
            self.worker.sig_log_message.disconnect()
        except:
            pass

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.combo_ports.setEnabled(True)
        self.combo_baud.setEnabled(True)

    def on_connection_error(self, msg):
        QMessageBox.critical(self, "Ошибка порта", msg)
        self.stop_reading()

    # ------------------------------------------------------------------------
    # ЛОГИКА ДАННЫХ (1-в-1 с Tkinter версией)
    # ------------------------------------------------------------------------
    def on_packet_received(self, packet_num, data, offset):
        self.packets_storage[packet_num] = {'data': data, 'offset': offset}
        # Если были события, ждущие этот пакет
        if packet_num in self.pending_events:
            events_list = self.pending_events.pop(packet_num)
            for evt in events_list:
                self.store_and_update_event(packet_num, evt['event_num'], data, evt['timestamp'], evt['tree_item'])
            self.status_bar.showMessage(f"Получен пакет #{packet_num}")

    def on_log_message(self, text):
        # Парсинг строки "Time | Time | Pack #... | ..."
        item = QTreeWidgetItem(self.tree)

        time_pc = datetime.now().strftime('%H:%M:%S')

        try:
            # Формат: RPi_Time | PC_Time | Pack #N Event M | Thr: K
            parts = [p.strip() for p in text.split('|')]

            # Если строка простая (не отформатирована на RPi), то пробуем парсить
            if len(parts) < 2:
                # Скорее всего это сырая строка с RPi, форматируем здесь
                time_rpi = text.split('|')[0] if '|' in text else text.split()[0]
                pack_str = text
            else:
                time_rpi = parts[0]

            # Попытка извлечь номера
            pack_num = None
            event_num = 1

            import re
            m_pack = re.search(r'(?:Pack|Pck)\s*#(\d+)', text)
            if m_pack: pack_num = int(m_pack.group(1))

            m_evt = re.search(r'Event\s*(\d+)', text)
            if m_evt: event_num = int(m_evt.group(1))

            info_text = f"Pck #{pack_num} Event {event_num}" if pack_num else text

            item.setText(0, time_rpi)
            item.setText(1, time_pc)
            item.setText(2, info_text)
            item.setText(3, self.combo_thr.currentText())

            if pack_num is not None:
                item.setData(0, Qt.ItemDataRole.UserRole, pack_num)
                item.setData(1, Qt.ItemDataRole.UserRole, event_num)

                if pack_num in self.packets_storage:
                    # Пакет уже есть
                    data = self.packets_storage[pack_num]['data']
                    self.store_and_update_event(pack_num, event_num, data, time_rpi, item)
                else:
                    # Ждем пакет
                    if pack_num not in self.pending_events:
                        self.pending_events[pack_num] = []
                    self.pending_events[pack_num].append({
                        'event_num': event_num,
                        'timestamp': time_rpi,
                        'tree_item': item,
                        'added_time': time.time()
                    })
                    # Серый цвет
                    item.setForeground(0, QBrush(QColor("gray")))
                    item.setForeground(2, QBrush(QColor("gray")))

        except Exception:
            item.setText(0, "?")
            item.setText(2, text)

        self.tree.scrollToBottom()

    def store_and_update_event(self, pack_num, ev_num, data, ts, item):
        key = (pack_num, ev_num)
        self.events_storage[key] = {'data': data, 'ts': ts}

        if item:
            item.setForeground(0, QBrush(QColor("black")))
            item.setForeground(2, QBrush(QColor("black")))

            max_val = int(np.max(np.abs(data))) if len(data) > 0 else 0
            curr_txt = item.text(2)
            if "Max" not in curr_txt:
                item.setText(2, f"{curr_txt} | Max: {max_val}")

    def check_pending_events(self):
        now = time.time()
        to_remove = []
        for p, ev_list in self.pending_events.items():
            if not ev_list: continue
            if now - ev_list[0]['added_time'] > self.pending_timeout:
                for evt in ev_list:
                    item = evt['tree_item']
                    if item:
                        item.setForeground(2, QBrush(QColor("red")))
                        item.setText(2, item.text(2) + " (TIMEOUT)")
                to_remove.append(p)
        for p in to_remove:
            del self.pending_events[p]

    def on_tree_click(self, item, col):
        pack_num = item.data(0, Qt.ItemDataRole.UserRole)
        event_num = item.data(1, Qt.ItemDataRole.UserRole)
        if pack_num is None: return

        key = (pack_num, event_num)
        if key in self.events_storage:
            self.plot_event(self.events_storage[key]['data'])
            self.stats_label.setText(f"Pack #{pack_num}.{event_num}")
        elif pack_num in self.packets_storage:
            self.plot_event(self.packets_storage[pack_num]['data'])
            self.stats_label.setText(f"Pack #{pack_num} (Raw)")

    def plot_event(self, data):
        self.plot_data_item.setData(data)
        self.plot_widget.enableAutoRange()

    # ------------------------------------------------------------------------
    # ОТПРАВКА ПОРОГА (ИСПРАВЛЕНО ПОД ТВОЙ КОД)
    # ------------------------------------------------------------------------
    def send_threshold_cmd(self):
        try:
            val_str = self.combo_thr.currentText()
            multiplier = int(val_str)
            # Твой протокол: 1 -> 'a', 2 -> 'b'
            char_code = chr(ord('a') + multiplier - 1)

            # Важно: в твоем Tkinter коде команда была "SET:x\r\n"
            msg = f"SET:{char_code}\r\n"

            self.worker.send_command(msg)
            self.status_bar.showMessage(f"Отправка: {multiplier} (код '{char_code}')")

            # Обновляем локально
            self.current_threshold = multiplier * 10000000
            self.lbl_thr_val.setText(f"Текущий: {self.current_threshold}")
            self.update_plot_threshold_lines()

        except Exception as e:
            QMessageBox.warning(self, "Ошибка", str(e))

    def on_threshold_update_from_uart(self, val):
        self.current_threshold = val
        self.lbl_thr_val.setText(f"Текущий (RPi): {val}")
        self.update_plot_threshold_lines()
        mult = val // 10000000
        if 1 <= mult <= 20:
            self.combo_thr.setCurrentText(str(mult))

    def update_plot_threshold_lines(self):
        self.line_thr_pos.setValue(self.current_threshold)
        self.line_thr_neg.setValue(-self.current_threshold)

    def clear_all(self):
        self.tree.clear()
        self.packets_storage.clear()
        self.events_storage.clear()
        self.pending_events.clear()
        self.plot_data_item.setData([])

    def closeEvent(self, event):
        self.stop_reading()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
