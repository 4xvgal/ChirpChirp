#main .py



def run_transmitter():
    pass


def run_receiver():
    pass

def choose_mode():
    print("LoRa SYSTEM")
    print("1. Transmitter")
    print("2. Receiver")
    choice = input("Choose mode (1 or 2): ")
    return choice

if __name__ == "__main__":
    mode = choose_mode()
    if mode == '1':
        run_transmitter()
    elif mode == '2':
        run_receiver()
    else:
        print("Invalid choice. Please run the program again.")