import mne
from mne.preprocessing import ICA
from autoreject import AutoReject
import numpy as np
import matplotlib.pyplot as plt

# WICHTIG: Import der Helper-Funktionen aus dem eigenen Modul
from src.xdf_loader import _build_annotations_from_xdf_marker, merge_duplicate_events

class EEGPreprocessor:
    def __init__(self):
        self.raw = None
        self.epochs = None

    def apply_initial_filter(self, raw):
        print("Entferne DC-Offset und wende initialen Bandpass-Filter (0.1-30 Hz) an.")
        raw.filter(l_freq=0.1, h_freq=30, fir_design='firwin')
        return raw


    def apply_preprocessing(self, raw, marker_stream=None, eeg_first_ts=None):
        # ... (Methoden-Code wie oben, hier bleibt er unverändert) ...
        self.raw = raw
        self.apply_montage()
        self.pick_channels()
        self.apply_filters_and_reference()
        self.apply_ica()
        self.create_epochs(marker_stream, eeg_first_ts)
        self.run_autoreject()
        return self.raw, self.epochs

    def apply_montage(self):
        # ... (Methoden-Code wie oben) ...
        try:
            montage = mne.channels.make_standard_montage('standard_1020')
            self.raw.set_montage(montage, on_missing='warn')
        except Exception:
            pass

    def pick_channels(self):
        # ... (Methoden-Code wie oben) ...
        exclude_channels = ['AF4', 'AF3', 'P1', 'P2', 'P3', 'P4', 'PO3', 'PO4', 'PB1', 'PB2', 'PB3', 'PB4', 'PB5',
                            'triggerStream', 'PPG', 'EDA']
        channels_to_keep = [ch for ch in self.raw.info['ch_names'] if ch not in exclude_channels]
        try:
            self.raw.pick(channels_to_keep)
            print("Kanäle ausgewählt und Bad-Kanäle interpoliert.")
            self.raw.interpolate_bads(reset_bads=True)
        except Exception as e:
            print(f"Fehler bei Kanalauswahl: {e} — fahre mit allen Kanälen fort.")

    def apply_filters_and_reference(self):
        # ... (Methoden-Code wie oben) ...
        self.raw.set_eeg_reference('average', projection=True)
        print("Durchschnittsreferenz gesetzt.")
        self.raw.filter(l_freq=0.1, h_freq=30, fir_design='firwin')
        print("Bandpass-Filter (0.1–30 Hz) angewendet.")

    def apply_ica(self):
        # ... (Methoden-Code wie oben) ...
        raw_ica = self.raw.copy().filter(l_freq=1.5, h_freq=None, fir_design='firwin')
        print("Hochpass-Filter (1.5 Hz) für ICA angewendet.")
        try:
            ica = ICA(n_components=0.95, random_state=97, max_iter=800, method='infomax')
            ica.fit(raw_ica)
            print("ICA-Modell (Infomax) trainiert.")
        except Exception as e:
            print(f"Fehler beim ICA-Fit: {e}, Fallback auf FastICA.")
            ica = ICA(n_components=0.95, random_state=97, max_iter=800, method='fastica')
            ica.fit(raw_ica)
            print("ICA-Modell (FastICA) trainiert.")

        # Artefakt-Komponenten finden und entfernen
        try:
            ecg_inds, _ = ica.find_bads_ecg(self.raw)
            if ecg_inds:
                ica.exclude.extend(ecg_inds)
                print(f"ECG-Komponenten entfernt: {ecg_inds}")
        except Exception:
            pass
        try:
            eog_inds, _ = ica.find_bads_eog(self.raw)
            if eog_inds:
                ica.exclude.extend(eog_inds)
                print(f"EOG-Komponenten entfernt: {eog_inds}")
        except Exception:
            pass

        ica.apply(self.raw)
        print("ICA-Korrektur angewendet.")

    def create_epochs(self, marker_stream, eeg_first_ts):
        # ... (Methoden-Code wie oben) ...
        if marker_stream is None:
            print("Kein Marker-Stream in der XDF-Datei gefunden. Epochen können nicht erstellt werden.")
            return

        try:
            ann, desc = _build_annotations_from_xdf_marker(marker_stream, eeg_first_ts, self.raw, keep_numeric_only=True)
            if ann is None:
                print("Keine gültigen Marker gefunden.")
                return

            self.raw.set_annotations(ann)
            uniq_desc = sorted(set(desc), key=lambda x: int(x))
            counts = {d: desc.count(d) for d in uniq_desc}
            print(f"Marker gefunden (numerisch): {', '.join(uniq_desc)}")
            print("Anzahl pro Marker: " + ", ".join([f"{k}={counts[k]}" for k in uniq_desc]))
            event_id_map = {d: int(d) for d in uniq_desc}
            events, _ = mne.events_from_annotations(self.raw, event_id=event_id_map)
            events = merge_duplicate_events(events)

            if events.size == 0:
                print("Keine Events gefunden.")
                return

            self.epochs = mne.Epochs(
                self.raw, events, event_id=event_id_map,
                tmin=-1.0, tmax=2.0, baseline=(None, 0),
                preload=True, on_missing="warn", proj=True
            )
            print(f"{len(self.epochs)} Epochen erstellt.")
        except Exception as e:
            print(f"Fehler bei Marker/Events/Epoching: {e}")
            self.epochs = None

    def run_autoreject(self):
        # ... (Methoden-Code wie oben) ...
        if self.epochs is None or len(self.epochs) == 0:
            print("Keine Epochen für AutoReject vorhanden.")
            return
        
        try:
            if self.raw.get_montage() is None:
                print("Warnung: Keine gültige Montage -> AutoReject übersprungen.")
                return
            
            ar = AutoReject(n_interpolate=[1, 2, 4], random_state=42, n_jobs=-1, verbose='tqdm')
            self.epochs = ar.fit_transform(self.epochs)
            print(f"AutoReject abgeschlossen. {len(self.epochs)} von {len(self.epochs)} Epochen behalten.")
        except Exception as e:
            print(f"Fehler bei AutoReject: {e} -> nutze Roh-Epochen.")