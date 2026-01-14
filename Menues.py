import Printer

"""Главное меню программы"""
def main_menu(main_prog, check_stream, view_buffer_packets, stop_stream, zig_ser):
    while True:
        Printer.menu_print()
        choice = input("Your choice: ").strip()
        if choice == "1":
            main_prog()
        elif choice == "2":
            check_stream()
        elif choice == "3":
            view_buffer_packets()
        elif choice == "4":
            stop_stream()
        elif choice == "5":
            print("\nClosing all ports...")
            zig_ser.close_serial()
            print("Exiting.\n")
            break
        else:
            print("\nWrong input, try again")