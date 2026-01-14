import serial, time, threading
import Menues, Printer
import Zigbee_Logic as ziglo
import Uart_Logic as uart

# ============================================================================
# КОНСТАНТЫ И КОНФИГУРАЦИЯA
# ============================================================================
START_BYTE = b'\x11'
STOP_BYTE = b'\x01'
START_PATTERN = b'\xB6' * 10
END_PATTERN = b'\x49' * 10
PACK_SIZE = 4799
MAX_PACKETS = 10
PEAK_THRESHOLD = 150000000

# Глобальный флаг для остановки
main_run_flag = True

# Экземпляры классов
uart_ser = uart.Serial_reader()
zig_ser = ziglo.ZigbeeSerial()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def check_stream():
    """Проверка потока данных в основном порте"""
    try:
        if uart_ser.main_ser and uart_ser.main_ser.is_open:
            n = uart_ser.main_ser.in_waiting
            if n > 0:
                print(f"\n[Check Stream] Data in flow: {n} bytes waiting to read.")
            else:
                print("\n[Check Stream] Flow is empty, no data.")
        else:
            print("\n[Check Stream] Port is closed.")
    except Exception as e:
        print(f"[ERROR] Unable to check flow: {e}")


def view_buffer_packets():
    """Просмотр событий в буфере - каждое как отдельная запись"""
    if len(zig_ser.peak_log) == 0:
        print("\n[View Buffer] No events in buffer yet.")
        return

    print(f"\n{Printer.DELIMETER}")
    print(f"[View Buffer] Recent events (total {len(zig_ser.peak_log)} events):")
    print(Printer.DELIMETER)

    for i, event in enumerate(zig_ser.peak_log[-15:], 1):
        timestamp = event.get('time', '?')
        packet_num = event.get('packet_num', '?')
        event_num = event.get('event_num', '?')
        total_in_packet = event.get('total_events_in_packet', '?')
        max_value = event.get('max_value', '?')
        duration = event.get('duration', '?')

        print(f"{i}. {timestamp} | Pack#{packet_num} | "
              f"Event {event_num}/{total_in_packet} | "
              f"Max={max_value:.0f} | Duration={duration}")

    print(Printer.DELIMETER)


def stop_stream():
    """Остановка потока"""
    global main_run_flag
    main_run_flag = False
    uart_ser.main_run_flag = False

    if uart_ser.main_ser and uart_ser.main_ser.is_open:
        uart_ser.main_ser.write(STOP_BYTE)
        uart_ser.main_ser.flush()
        print(f"\n[Stop Stream] {time.strftime('%H:%M:%S')} - Stop byte sent")
    else:
        print("\n[Stop Stream] Port is closed.")

    time.sleep(0.5)


# ============================================================================
# ОСНОВНАЯ ПРОГРАММА
# ============================================================================

def main_program():
    """Основная программа"""
    global main_run_flag

    uart_ser.main_ring_que.clear()
    uart_ser.main_packet_info.clear()
    zig_ser.peak_log.clear()
    uart_ser.main_total_packets = 0
    uart_ser.main_run_flag = True
    main_run_flag = True

    try:
        # ШАГ 1: Инициализируем Zigbee
        print("[Init] Initializing Zigbee module...")
        if not zig_ser.init_serial():
            print("[Warning] Zigbee initialization failed, continuing without it...")
        else:
            print("[Init] ✓ Zigbee initialized")

        time.sleep(0.5)

        # ШАГ 2: Открываем основной UART порт
        print("[Init] Opening main UART port...")
        try:
            uart_ser.main_ser = serial.Serial(uart_ser.serial_port, uart_ser.baud_rate, timeout=0.1)
            print(f"[Init] ✓ Main port opened: {uart_ser.serial_port} at {uart_ser.baud_rate} baud")
        except Exception as e:
            print(f"[ERROR] Failed to open main port: {e}")
            return

        # Отправляем стартовый байт
        uart_ser.main_ser.write(START_BYTE)
        uart_ser.main_ser.flush()
        print(f"[Init] {time.strftime('%H:%M:%S')} - Start byte sent, extracting data...\n")
        print("[Info] Press Enter in terminal to stop...\n")
        Printer.printHeader('Данные')

        # ШАГ 3: Запускаем поток чтения
        # ВАЖНО: правильные параметры для новой версии!
        thread = threading.Thread(
            target=uart_ser.main_serial_reader,
            args=(zig_ser, PEAK_THRESHOLD, STOP_BYTE),
            daemon=True,
            name="UARTReaderThread"
        )
        thread.start()

        # Ждем завершения потока (он может завершиться сам или по сигналу пользователя)
        thread.join()

    except KeyboardInterrupt:
        print("\n[INFO] KeyboardInterrupt received")
        uart_ser.main_run_flag = False

    except Exception as e:
        print(f"\n[ERROR] Starting program error: {e}")
        import traceback
        traceback.print_exc()
        uart_ser.main_run_flag = False

    finally:
        uart_ser.main_run_flag = False
        print("\n[Cleanup] Closing all ports...")

        if uart_ser.main_ser and uart_ser.main_ser.is_open:
            try:
                uart_ser.main_ser.close()
                print("[Cleanup] ✓ Main port closed")
            except:
                pass

        try:
            zig_ser.close_serial()
            print("[Cleanup] ✓ Zigbee port closed")
        except:
            pass

        print("\n")
        Printer.print_result(uart_ser.main_total_packets, zig_ser.peak_log, PEAK_THRESHOLD)


# ============================================================================
# ГЛАВНОЕ МЕНЮ И ТОЧКА ВХОДА
# ============================================================================

if __name__ == "__main__":
    print("Starting program in 3s...\n")
    for i in range(3, 0, -1):
        print(f'{i}...')
        time.sleep(1)

    print('\n')
    main_program()

    # После основной программы показываем меню
    print("\n[Menu] Starting interactive menu...\n")
    Menues.main_menu(main_program, check_stream, view_buffer_packets, stop_stream, zig_ser)
