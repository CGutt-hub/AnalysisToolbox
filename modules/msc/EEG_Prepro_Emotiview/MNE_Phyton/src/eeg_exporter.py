import os
import mne

class EEGExporter:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def save_raw(self, raw, filename):
        # ... (Methoden-Code wie oben) ...
        raw_output_path = os.path.join(self.output_dir, f'{filename}_preprocessed_raw.fif')
        raw.save(raw_output_path, overwrite=True)
        print(f"Bereinigte Rohdaten gespeichert: {raw_output_path}")

    def save_epochs(self, epochs, filename):
        # ... (Methoden-Code wie oben) ...
        if epochs is not None:
            epochs_output_path = os.path.join(self.output_dir, f'{filename}_epoched-epo.fif')
            epochs.save(epochs_output_path, overwrite=True)
            print(f"Bereinigte Epochen gespeichert: {epochs_output_path}")