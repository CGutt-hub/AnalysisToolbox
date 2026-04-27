import os
from collections import defaultdict

import mne
import numpy as np
from pyxdf import load_xdf


# --- Helper-Funktionen -------------------------------------------------------


def _as_str(x):
    """Robuste Umwandlung beliebiger Marker-/Header-Werte in String."""
    if isinstance(x, (list, tuple, np.ndarray)) and len(x) > 0:
        x = x[0]
    if isinstance(x, bytes):
        try:
            x = x.decode('utf-8', 'ignore')
        except Exception:
            x = str(x)
    return str(x)


def _find_marker_stream(xdf_data):
    """Heuristik: finde einen Marker-/Stimulus-Stream in xdf_data."""
    cand = None
    for s in xdf_data:
        t = s['info'].get('type', [''])[0].lower()
        n = s['info'].get('name', [''])[0].lower()
        if any(k in t for k in ['marker', 'markers', 'stim', 'event']) or \
           any(k in n for k in ['marker', 'markers', 'stim', 'event']):
            cand = s
            break
    return cand


def merge_duplicate_events(events):
    """Falls mehrere Events auf exakt demselben Sample landen: zusammenfassen."""
    merged = []
    sample_to_event = defaultdict(list)
    for ev in events:
        sample_to_event[ev[0]].append(ev[2])
    for sample, ev_list in sample_to_event.items():
        merged.append([sample, 0, ev_list[0]])
    return np.array(merged, dtype=int)


def _build_annotations_from_xdf_marker(
    marker_stream,
    eeg_first_ts,
    raw,
    keep_numeric_only=True
):
    """Erzeuge MNE-Annotations aus XDF-Markerstream."""
    onset_sec = np.array(marker_stream['time_stamps'], dtype=float) - float(
        eeg_first_ts
    )
    desc = [_as_str(v) for v in marker_stream['time_series']]
    dur = raw.n_times / raw.info['sfreq']

    valid_mask = (onset_sec >= 0) & (onset_sec <= (dur - 1e-6))
    onset_sec = onset_sec[valid_mask]
    desc = [d for d, m in zip(desc, valid_mask) if m]

    if keep_numeric_only:
        desc_num = []
        onsets_num = []
        for d, t in zip(desc, onset_sec):
            try:
                _ = int(d)
                desc_num.append(str(int(d)))
                onsets_num.append(float(t))
            except Exception:
                continue
        desc = desc_num
        onset_sec = np.array(onsets_num, dtype=float)

    if len(desc) == 0:
        return None, None

    ann = mne.Annotations(
        onset=onset_sec.tolist(),
        duration=[0.0] * len(onset_sec),
        description=desc
    )
    return ann, desc


# --- Hauptklasse -------------------------------------------------------------


class XDFLoader:
    def __init__(self, file_path):
        self.file_path = file_path
        self.xdf_data = None
        self.eeg_stream = None
        self.marker_stream = None

    def load_data(self):
        """Lade XDF und finde EEG- sowie Marker-Stream."""
        print(
            f"--- Starte Verarbeitung für: "
            f"{os.path.basename(self.file_path).replace('.xdf', '')} ---"
        )
        try:
            self.xdf_data, _ = load_xdf(self.file_path)
            self.eeg_stream = next(
                s for s in self.xdf_data
                if s['info']['type'][0].lower() == 'eeg'
            )
            print("XDF-Datei erfolgreich geladen.")
            self.marker_stream = _find_marker_stream(self.xdf_data)
        except (StopIteration, FileNotFoundError) as e:
            print(f"Fehler beim Laden der Datei oder Finden des Streams: {e}")
            self.eeg_stream = None
            self.marker_stream = None
        return self.eeg_stream is not None

    def get_raw(self):
        print("DEBUG: get_raw() wurde aufgerufen")
        """Erzeuge mne.io.RawArray aus EEG-Stream (inkl. Einheiten-Check)."""
        if self.eeg_stream is None:
            print("DEBUG: Kein eeg_stream vorhanden → None zurückgegeben")

            return None

        # Kanalnamen extrahieren
        ch_labels = [
            ch['label'][0]
            for ch in self.eeg_stream['info']['desc'][0]['channels'][0]['channel']
        ]
        sfreq = float(self.eeg_stream['info']['nominal_srate'][0])

        # Kanaltypen setzen (vereinfachte Heuristik: letzter Kanal = ECG, falls vorhanden)
        ecg_candidates = [
            c for c in ch_labels if 'EKG' in c.upper() or 'ECG' in c.upper()
        ]
        if ecg_candidates:
            ch_types = ['eeg'] * (len(ch_labels) - 1) + ['ecg']
        else:
            ch_types = ['eeg'] * len(ch_labels)

        # Datenmatrix extrahieren (T = Samples x Channels)
        raw_data = np.array(self.eeg_stream['time_series'], dtype=float).T

        # Einheiten-/Skalierungscheck
        absmax = np.nanmax(np.abs(raw_data))
        print(
            f"EEG-Datenbereich vor Skalierung: "
            f"{np.nanmin(raw_data):.6f} ... {np.nanmax(raw_data):.6f}"
        )

        # Heuristik:
        # - Wenn Werte >> 1000, dann vermutlich µV → in Volt umrechnen.
        # - Wenn Werte sehr klein (<1e-3), vermutlich schon Volt.
        # - Dazwischen: unklar → Hinweis ausgeben.
        if absmax > 1e3:
            raw_data *= 1e-6
            print("Skalierung angewendet: Werte von µV → V (× 1e-6).")
        elif absmax < 1e-3:
            print("Keine Skalierung nötig (Werte vermutlich in Volt).")
        else:
            print("⚠️ Ungewöhnlicher Wertebereich – bitte Einheiten prüfen.")

        # MNE-Objekt erzeugen
        info = mne.create_info(
            ch_names=ch_labels,
            sfreq=sfreq,
            ch_types=ch_types
        )
        raw = mne.io.RawArray(raw_data, info)
        raw._data -= np.mean(raw._data, axis=1, keepdims=True)
        print("DC-Offset pro Kanal entfernt (Python/MNE).")

        return raw

    def get_marker_info(self):
        """Gibt Marker-Stream und ersten EEG-Zeitstempel zurück."""
        if self.marker_stream is None:
            return None, None
        eeg_first_ts = float(self.eeg_stream['time_stamps'][0])
        return self.marker_stream, eeg_first_ts
