DELIMETER = '----------------------------------------------------'

EN_DELIMETER = '-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-'


def printHeader(string):
    print(f'=====================================================\n'
          f'------------ {string} ------------\n'
          f'=====================================================')


def menu_print():
    print(f"\n{DELIMETER}")
    print("\n\t\t--- MENU ---\n")
    print(DELIMETER)
    print("1 - Start again")
    print("2 - Check flow")
    print("3 - Check packets in buffer")
    print("4 - Stop flow")
    print("5 - Exit program")
    print(DELIMETER)


def print_result(main_total_packets, zigbee_peak_log, peak_threshold):
    """Вывод итогов работы - каждое событие как отдельная запись"""

    print(f'\n\n{DELIMETER}')
    print(EN_DELIMETER)
    print(DELIMETER)
    print("\n\t\t\t--- Result ---\n")
    print(DELIMETER)

    print(f"Total Packets Processed: {main_total_packets}")
    print(DELIMETER)

    total_events = len(zigbee_peak_log)
    print(f"Total Events Detected (threshold {peak_threshold}): {total_events}")
    print(DELIMETER)

    if total_events > 0:
        print("\n\t\t-- Event Log (Last 30) --\n")

        for i, event in enumerate(zigbee_peak_log[-30:], 1):
            timestamp = event.get('time', '?')
            packet_num = event.get('packet_num', '?')
            event_num = event.get('event_num', '?')
            total_in_packet = event.get('total_events_in_packet', '?')
            max_value = event.get('max_value', '?')
            duration = event.get('duration', '?')

            print(f"{i}. {timestamp} | Pack#{packet_num} | "
                  f"Event {event_num}/{total_in_packet} | "
                  f"Max={max_value:.0f} | Duration={duration}")

        print(DELIMETER)

        # Статистика
        max_values = [e['max_value'] for e in zigbee_peak_log]
        durations = [e['duration'] for e in zigbee_peak_log]

        print(f"\nStatistics:")
        print(f"  • Total events: {total_events}")
        print(f"  • Max value overall: {max(max_values):.0f}")
        print(f"  • Min value overall: {min(max_values):.0f}")
        print(f"  • Avg value: {sum(max_values) / len(max_values):.0f}")
        print(f"  • Avg duration: {sum(durations) / len(durations):.0f} samples")
        print(DELIMETER)
    else:
        print("No events were detected during the session.")
        print(DELIMETER)
