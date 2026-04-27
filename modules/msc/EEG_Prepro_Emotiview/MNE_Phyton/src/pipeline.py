import os
import glob
import matplotlib.pyplot as plt

# WICHTIG: Import der Klassen aus den eigenen Modulen
from src.xdf_loader import XDFLoader
from src.eeg_preprocessor import EEGPreprocessor
from src.eeg_exporter import EEGExporter

class PreProcessingPipeline:
    def __init__(self, path_in, path_out, path_stat, path_plot):
        self.path_in = path_in
        self.path_out = path_out
        self.path_stat = path_stat
        self.path_plot = path_plot
        self.ensure_directories()

    def ensure_directories(self):
        # ... (Methoden-Code wie oben) ...
        os.makedirs(self.path_out, exist_ok=True)
        os.makedirs(self.path_stat, exist_ok=True)
        os.makedirs(self.path_plot, exist_ok=True)
        print("Ausgabe-Verzeichnisse überprüft/erstellt.")

    def run_pipeline(self):
        # ... (Methoden-Code wie oben) ...
        file_list = glob.glob(os.path.join(self.path_in, '*.xdf'))
        if not file_list:
            print(f"Keine .xdf-Dateien in {self.path_in} gefunden.")
            return

        for file_path in file_list:
            try:
                self.process_single_file(file_path)
            except Exception as e:
                print(f"!!!!!! Fehler bei {os.path.basename(file_path)}: {e} !!!!!!")

    def process_single_file(self, file_path):
        # ... (Methoden-Code wie oben) ...
        filename = os.path.basename(file_path).replace('.xdf', '')

        # Laden der Daten
        loader = XDFLoader(file_path)
        if not loader.load_data():
            return

        raw_data = loader.get_raw()
        if raw_data is None:
            return

        # Rohdaten speichern (unverarbeitet)
        raw_raw_output_path = os.path.join(self.path_out, f'{filename}_raw.fif')
        raw_data.save(raw_raw_output_path, overwrite=True)
        print(f"Unverarbeitete Rohdaten gespeichert: {raw_raw_output_path}")

        # Verarbeitung der Daten
        preprocessor = EEGPreprocessor()
        marker_stream, eeg_first_ts = loader.get_marker_info()
        raw_preprocessed, epochs_clean = preprocessor.apply_preprocessing(
            raw=raw_data,
            marker_stream=marker_stream,
            eeg_first_ts=eeg_first_ts
        )

        # Speichern
        exporter = EEGExporter(self.path_out)
        exporter.save_raw(raw_preprocessed, filename)
        exporter.save_epochs(epochs_clean, filename)

        # Visualisierung
        if epochs_clean is not None:
            print("Zeige epoched Daten...")
            epochs_clean.plot(n_channels=30, block=True)
        print("Starte interaktives Plotten der EEG-Ableitungen...")
        raw_preprocessed.plot(n_channels=30, block=True, scalings='auto', title=f'Bereinigte EEG-Daten: {filename}')

        print(f"--- Verarbeitung für {filename} abgeschlossen. ---")