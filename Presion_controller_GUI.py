import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel, QLineEdit, QCheckBox
from PyQt5.QtCore import QTimer, pyqtSignal
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import pyfirmata2
import csv
import time
import threading
from pyfirmata2 import util
import matplotlib.pyplot as plt

class SensorApp(QMainWindow):
    stop_signal = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.board = pyfirmata2.Arduino(pyfirmata2.Arduino.AUTODETECT)
        self.board.sp.baudrate = 57600
        time.sleep(0.3)

        self.max_data_size = 100
        self.setWindowTitle("Visualización sensores")
        self.setGeometry(100, 100, 800, 600)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.layout = QVBoxLayout(self.central_widget)

        self.fig, self.ax = plt.subplots()
        self.canvas = FigureCanvas(self.fig)
        self.layout.addWidget(self.canvas)

        self.x_pressure_data, self.y_pressure_data = [], []
        self.line2, = self.ax.plot(self.x_pressure_data, self.y_pressure_data, 'r-')
        self.setpoint_line, = self.ax.plot([], [], 'g--')
        self.ax.set_xlabel('Tiempo (s)')
        self.ax.set_ylabel('Presion (kPa)')

        self.start_button = QPushButton("Correr programa", self)
        self.stop_button = QPushButton("Detener", self)
        self.layout.addWidget(self.start_button)
        self.layout.addWidget(self.stop_button)

        self.start_button.clicked.connect(self.start_data_collection)
        self.stop_button.clicked.connect(self.stop_data_collection)

        self.pressurePIN = self.board.analog[1]
        self.pressurePIN.enable_reporting()
        self.presion_max = 700

        self.it = util.Iterator(self.board)
        self.it.start()

        self.pressure_buf = [0]
        self.pressure_data = []

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_graph)
        self.timer.start(800)

        self.start_time = time.time()
        self.is_collecting_data = False
        self.is_pressure_control_active = False  # Nuevo flag para control de presión
        self.pressure_controller_thread = None
        self.current_setpoint = 0

        # Agregar elementos para el control de presión
        self.setpoint_label = QLabel("Setpoint (kPa):", self)
        self.setpoint_input = QLineEdit(self)
        self.enable_checkbox = QCheckBox("Activar control de presión", self)
        self.send_button = QPushButton("Enviar", self)
        self.layout.addWidget(self.setpoint_label)
        self.layout.addWidget(self.setpoint_input)
        self.layout.addWidget(self.enable_checkbox)
        self.layout.addWidget(self.send_button)

        # Conectar el botón "Enviar" a la función correspondiente
        self.send_button.clicked.connect(self.send_setpoint)

        # Agregar botones para abrir/cerrar válvulas
        self.valve_inlet_button = QPushButton("Abrir válvula de entrada", self)
        self.valve_outlet_button = QPushButton("Abrir válvula de salida", self)
        self.layout.addWidget(self.valve_inlet_button)
        self.layout.addWidget(self.valve_outlet_button)

        self.valve_inlet_button.clicked.connect(self.toggle_inlet_valve)
        self.valve_outlet_button.clicked.connect(self.toggle_outlet_valve)

        # Deshabilitar botones de válvulas al inicio
        self.set_valve_buttons_enabled(True)

        # Campos de texto para mostrar tiempo y presión
        self.time_label = QLabel("Tiempo: ", self)
        self.pressure_label = QLabel("Presión: ", self)
        self.layout.addWidget(self.time_label)
        self.layout.addWidget(self.pressure_label)

        self.lock = threading.Lock()

    def set_valve_buttons_enabled(self, enabled):
        self.valve_inlet_button.setEnabled(enabled)
        self.valve_outlet_button.setEnabled(enabled)

    def send_setpoint(self):
        try:
            setpoint = float(self.setpoint_input.text())
            if setpoint > self.presion_max:
                print(f"El valor del setpoint no puede ser mayor que {self.presion_max} kPa.")
                return
            enabled = self.enable_checkbox.isChecked()
            if enabled:
                if self.pressure_controller_thread and self.pressure_controller_thread.is_alive():
                    self.stop_pressure_controller()  # Asegúrate de detener el controlador de presión anterior
                self.current_setpoint = setpoint  # Establecer el nuevo setpoint
                self.pressure_controller_thread = threading.Thread(target=self.start_pressure_controller, args=(setpoint,), daemon=True)
                self.is_pressure_control_active = True  # Activar el control de presión
                self.pressure_controller_thread.start()
                self.set_valve_buttons_enabled(False)
            else:
                self.stop_pressure_controller()
                self.set_valve_buttons_enabled(True)
        except ValueError:
            print("Por favor, ingrese un valor válido para el setpoint.")

    def start_data_collection(self):
        self.is_collecting_data = True
        t = threading.Thread(target=self.collect_data)
        t.start()

    def stop_data_collection(self):
        with self.lock:
            self.is_collecting_data = False
        self.save_data_to_csv()

    def calculate_presion(self, presion, presion_max):
        avg_value = sum(presion) / len(presion)
        presion_L = max(0, min(presion_max, avg_value))
        return presion_L

    def collect_data(self):
        try:
            while True:
                with self.lock:
                    if not self.is_collecting_data:
                        break

                presion_sensor = self.pressurePIN.read()

                if presion_sensor is not None:
                    pressure_N = max(0, min(1, presion_sensor))
                else:
                    pressure_N = 0

                press = pressure_N * self.presion_max

                self.pressure_buf.append(press)

                with self.lock:
                    self.pressure_value = self.calculate_presion(self.pressure_buf, self.presion_max)

                if len(self.pressure_buf) > 7:
                    self.pressure_buf.pop(0)

                pressure_current_time = time.time() - self.start_time
                self.pressure_data.append([pressure_current_time, self.pressure_value])

                if len(self.pressure_buf) > 7:
                    self.pressure_buf.pop(0)

                time.sleep(0.6)

        except KeyboardInterrupt:
            print("Se ha interrumpido el programa. Guardando datos en el archivo CSV...")
        finally:
            self.save_data_to_csv()

    def save_data_to_csv(self):
        try:
            with open('Datos_presion.csv', 'w', newline='') as csvfile:
                csvwriter = csv.writer(csvfile)
                csvwriter.writerow(['Tiempo', 'Presión'])
                csvwriter.writerows(self.pressure_data)
        finally:
            self.board.exit()

    def update_graph(self):
        if self.pressure_data:
            x_pressure_data = [point[0] for point in self.pressure_data]
            y_pressure_data = [point[1] for point in self.pressure_data]
            self.line2.set_xdata(x_pressure_data)
            self.line2.set_ydata(y_pressure_data)

            try:
                setpoint = float(self.setpoint_input.text())
            except ValueError:
                setpoint = 0  # En caso de que no se pueda convertir a float

            self.setpoint_line.set_xdata(x_pressure_data)
            self.setpoint_line.set_ydata([setpoint] * len(x_pressure_data))

            self.ax.relim()
            self.ax.autoscale_view()

            # Añadir leyenda
            self.ax.legend(['Presion (kPa)', 'Setpoint'], loc='upper right')

            self.canvas.draw()

            # Actualizar etiquetas de tiempo y presión
            if self.pressure_data:
                last_time, last_pressure = self.pressure_data[-1]
                self.time_label.setText(f"Tiempo: {last_time:.2f} s")
                self.pressure_label.setText(f"Presión: {last_pressure:.2f} kPa")

    def start_pressure_controller(self, setpoint):
        relay_inlet_pin = 9
        relay_outlet_pin = 10

        try:
            relay_inlet = self.board.digital[relay_inlet_pin]
            relay_outlet = self.board.digital[relay_outlet_pin]

            pressure_tolerance = 5

            while self.is_pressure_control_active:
                with self.lock:
                    current_pressure = self.pressure_value
                    print("Presión actual:", current_pressure)

                if current_pressure < setpoint - pressure_tolerance:
                    self.safe_write(relay_inlet, 1)
                    self.safe_write(relay_outlet, 0)
                    print("Válvula entrada abierta")

                elif current_pressure > setpoint + pressure_tolerance:
                    self.safe_write(relay_outlet, 1)
                    self.safe_write(relay_inlet, 0)
                    print("Válvula salida abierta")

                else:
                    self.safe_write(relay_inlet, 0)
                    self.safe_write(relay_outlet, 0)

                time.sleep(0.5)

        except Exception as e:
            print(f"Error en el controlador de presión: {e}")

        finally:
            self.safe_write(relay_inlet, 0)
            self.safe_write(relay_outlet, 0)
            print("Control de presión terminado.")

    def safe_write(self, pin, value):
        if self.board.sp.isOpen():
            pin.write(value)

    def stop_pressure_controller(self):
        with self.lock:
            self.is_pressure_control_active = False  # Detener el control de presión
        if self.pressure_controller_thread and self.pressure_controller_thread.is_alive():
            self.pressure_controller_thread.join()
            self.pressure_controller_thread = None

        # Restablecer el estado de las válvulas al detener el controlador de presión
        relay_inlet_pin = self.board.digital[9]
        relay_outlet_pin = self.board.digital[10]
        self.safe_write(relay_inlet_pin, 0)
        self.safe_write(relay_outlet_pin, 0)

    def toggle_inlet_valve(self):
        if not self.enable_checkbox.isChecked():
            relay_inlet_pin = self.board.digital[9]
            if self.valve_inlet_button.text() == "Abrir válvula de entrada":
                self.safe_write(relay_inlet_pin, 1)
                self.valve_inlet_button.setText("Cerrar válvula de entrada")
                print("Válvula de entrada abierta")
            else:
                self.safe_write(relay_inlet_pin, 0)
                self.valve_inlet_button.setText("Abrir válvula de entrada")
                print("Válvula de entrada cerrada")

    def toggle_outlet_valve(self):
        if not self.enable_checkbox.isChecked():
            relay_outlet_pin = self.board.digital[10]
            if self.valve_outlet_button.text() == "Abrir válvula de salida":
                self.safe_write(relay_outlet_pin, 1)
                self.valve_outlet_button.setText("Cerrar válvula de salida")
                print("Válvula de salida abierta")
            else:
                self.safe_write(relay_outlet_pin, 0)
                self.valve_outlet_button.setText("Abrir válvula de salida")
                print("Válvula de salida cerrada")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SensorApp()
    window.stop_signal.connect(app.quit)
    window.show()
    sys.exit(app.exec_())