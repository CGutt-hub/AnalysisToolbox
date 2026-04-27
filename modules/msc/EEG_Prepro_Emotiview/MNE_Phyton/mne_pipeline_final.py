import os
import glob
import mne
from mne.preprocessing import ICA
import matplotlib.pyplot as plt
import numpy as np
from pyxdf import load_xdf
from autoreject import AutoReject
from collections import defaultdict


# ----------------------------- Helper ---------------------------------

def _as_str(x):
    """Robust: Marker-Payload -> String."""
    if isinstance(x, (list, tuple, np.ndarray)) and len(x) > 0:
        x = x[0]
    if isinstance(x, bytes):
        try:
            x = x.decode('utf-8', 'ignore')
        except Exception:
            x = str(x)
    return str(x)


def _find_marker_stream(xdf_data):
    """Finde einen plausiblen Marker-/Trigger-Stream in den XDF-Daten."""
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
    """Fasse doppelte Events zusammen, die auf derselben Sample-Position liegen.
    Wenn mehrere Marker an genau der gleichen Zeit auftreten, bleibt nur einer erhalten.
    """
    merged = []
    sample_to_event = defaultdict(list)
    for ev in events:
        sample_to_event[ev[0]].append(ev[2])
    for sample, ev_list in sample_to_event.items():
        merged.append([sample, 0, ev_list[0]])  # nimm den ersten Event-Code
    return np.array(merged, dtype=int)

def _build_annotations_from_xdf_marker(marker_stream, eeg_first_ts, raw, keep_numeric_only=True):
    """Erzeuge MNE-Annotations aus einem Markerstream."""
    onset_sec = np.array(marker_stream['time_stamps'], dtype=float) - float(eeg_first_ts)
    desc = [_as_str(v) for v in marker_stream['time_series']]

    # nur gültige Marker behalten
    dur = raw.n_times / raw.info['sfreq']
    valid_mask = (onset_sec >= 0) & (onset_sec <= (dur - 1e-6))
    onset_sec = onset_sec[valid_mask]
    desc = [d for d, m in zip(desc, valid_mask) if m]

    # nur numerische Marker
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

    ann = mne.Annotations(onset=onset_sec.tolist(),
                          duration=[0.0] * len(onset_sec),
                          description=desc)
    return ann, desc

# ------------------------ Haupt-Pipeline --------------------------------

def preprocess_eeg_file(input_file_path, out_dir, stat_dir, plot_dir):
    FILENAME = os.path.basename(input_file_path).replace('.xdf', '')
    print(f"\n--- Starte Verarbeitung für: {FILENAME} ---")

    # --- 1) XDF laden & EEG-Stream finden ---
    try:
        xdf_data, _ = load_xdf(input_file_path)
        eeg_stream = next(s for s in xdf_data if s['info']['type'][0].lower() == 'eeg')
    except (StopIteration, FileNotFoundError) as e:
        print(f"Fehler beim Laden der Datei oder Finden des EEG-Streams: {e}")
        return

    ch_labels = [ch['label'][0] for ch in eeg_stream['info']['desc'][0]['channels'][0]['channel']]
    sfreq = float(eeg_stream['info']['nominal_srate'][0])

    # Kanaltypen
    ecg_candidates = [c for c in ch_labels if 'EKG' in c.upper() or 'ECG' in c.upper()]
    if ecg_candidates:
        ch_types = ['eeg'] * (len(ch_labels) - 1) + ['ecg']
    else:
        ch_types = ['eeg'] * len(ch_labels)

    info = mne.create_info(ch_names=ch_labels, sfreq=sfreq, ch_types=ch_types)
    
    raw_data = np.array(eeg_stream['time_series']).T
    raw = mne.io.RawArray(raw_data, info)
    print("EEG-Daten erfolgreich in MNE Raw-Objekt geladen.")

    # Rohdaten speichern (unverarbeitet)
    raw_raw_output_path = os.path.join(out_dir, f'{FILENAME}_raw.fif')
    raw.save(raw_raw_output_path, overwrite=True)
    print(f"Unverarbeitete Rohdaten gespeichert: {raw_raw_output_path}")


    # Montage (falls Namen passen)
    try:
        montage = mne.channels.make_standard_montage('standard_1020')
        raw.set_montage(montage, on_missing='warn')
    except Exception:
        pass

    # --- 2) Kanäle auswählen ---
    exclude_channels = ['AF4','AF3','P1','P2','P3','P4','PO3','PO4','PB1', 'PB2', 'PB3', 'PB4', 'PB5','triggerStream','PPG','EDA']
    channels_to_keep = [ch for ch in raw.info['ch_names'] if ch not in exclude_channels]

    try:
        raw.pick(channels_to_keep)
        print(f"Kanäle ausgewählt. Behaltene Kanäle: {', '.join(raw.ch_names)}")
    except Exception as e:
        print(f"Fehler bei Kanalauswahl: {e} — fahre mit allen Kanälen fort.")

    try:
        raw.interpolate_bads(reset_bads=True)
        print("Fehlende Kanäle interpoliert (falls vorhanden).")
    except Exception:
        pass

    # --- 3) Filter & Referenz ---
    raw.set_eeg_reference('average', projection=True)
    print("Durchschnittsreferenz gesetzt (Proj.).")
    raw.filter(l_freq=0.1, h_freq=30, fir_design='firwin')
    print("Bandpass-Filter (0.1–30 Hz) angewendet.")

    # --- 4) ICA ---
    raw_ica = raw.copy().filter(l_freq=1.5, h_freq=None, fir_design='firwin')
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

    # Artefakt-Komponenten (optional, robust ohne Kanalnamen)
    try:
        ecg_inds, _ = ica.find_bads_ecg(raw)
        if ecg_inds:
            ica.exclude.extend(ecg_inds)
            print(f"EKG-Komponenten entfernt: {ecg_inds}")
    except Exception:
        pass
    try:
        eog_inds, _ = ica.find_bads_eog(raw)
        if eog_inds:
            ica.exclude.extend(eog_inds)
            print(f"EOG-Komponenten entfernt: {eog_inds}")
    except Exception:
        pass

    ica.apply(raw)
    print("ICA-Korrektur angewendet.")
    
    
    # --- 4) Marker -> Annotations -> Events -> Epochs ---
    epochs = None
    try:
        marker_stream = _find_marker_stream(xdf_data)
        if marker_stream is None:
            print("Kein Marker-Stream in der XDF-Datei gefunden.")
        else:
            eeg_first_ts = float(eeg_stream['time_stamps'][0])
            ann, desc = _build_annotations_from_xdf_marker(
                marker_stream, eeg_first_ts, raw, keep_numeric_only=True
            )
            if ann is None:
                print("Keine gültigen Marker gefunden.")
            else:
                raw.set_annotations(ann)
                uniq_desc = sorted(set(desc), key=lambda x: int(x))
                counts = {d: desc.count(d) for d in uniq_desc}
                print(f"Marker gefunden (numerisch): {', '.join(uniq_desc)}")
                print("Anzahl pro Marker: " + ", ".join([f"{k}={counts[k]}" for k in uniq_desc]))

                event_id_map = {d: int(d) for d in uniq_desc}
                events, _ = mne.events_from_annotations(
                    raw, event_id=event_id_map
                )
                #events = merge_duplicate_events(events)
        

                if events.size == 0:
                    print("Keine Events gefunden.")
                else:
                    epochs = mne.Epochs(
                        raw, events, event_id=event_id_map,
                        tmin=-1.0, tmax=2.0,
                        baseline=(-0.2, 0.0),
                        preload=True, on_missing="warn", proj=True
                    )
                    print(f"{len(epochs)} Epochen erstellt.")
    except Exception as e:
        print(f"Fehler bei Marker/Events/Epoching: {e}")

    # --- 5) AutoReject ---
    epochs_clean = None
    if epochs is not None and len(epochs) > 0:
        try:
            if raw.get_montage() is None:
                print("Warnung: Keine gültige Montage -> AutoReject übersprungen.")
                epochs_clean = epochs
            else:
                ar = AutoReject(n_interpolate=[1, 2, 4], random_state=42, n_jobs=-1, verbose='tqdm')
                epochs_clean = ar.fit_transform(epochs)
                print(f"AutoReject abgeschlossen. {len(epochs_clean)} von {len(epochs)} Epochen behalten.")
        except Exception as e:
            print(f"Fehler bei AutoReject: {e} -> nutze Roh-Epochen.")
            epochs_clean = epochs
    else:
        print("Keine Epochen für AutoReject vorhanden.")

    # --- 6) Speichern ---
    os.makedirs(out_dir, exist_ok=True)
    raw_output_path = os.path.join(out_dir, f'{FILENAME}_preprocessed_raw.fif')
    raw.save(raw_output_path, overwrite=True)
    print(f"Bereinigte Rohdaten gespeichert: {raw_output_path}")

    if epochs_clean is not None:
        epochs_output_path = os.path.join(out_dir, f'{FILENAME}_epoched-epo.fif')
        epochs_clean.save(epochs_output_path, overwrite=True)
        print(f"Bereinigte Epochen gespeichert: {epochs_output_path}")
        # auch anzeigen
        print("Zeige epoched Daten...")
        epochs_clean.plot(n_channels=30, block=True)
    else:
        print("Keine Epochen gespeichert.")

    # --- 7) Interaktive Visualisierung ---
    print("Starte interaktives Plotten der EEG-Ableitungen...")
    raw.plot(n_channels=30, block=True, scalings='auto', title=f'Bereinigte EEG-Daten: {FILENAME}')
    print(f"--- Verarbeitung für {FILENAME} abgeschlossen. ---")


if __name__ == '__main__':
    PATH = 'D:/Bens Dateien/Uni/FH/EmotiView/'
    PATH_IN = os.path.join(PATH, 'data/raw/')
    PATH_OUT = os.path.join(PATH, 'data/pre_python/')
    PATH_STAT = os.path.join(PATH, 'stats/')
    PATH_PLOT = os.path.join(PATH, 'plots/')

    os.makedirs(PATH_OUT, exist_ok=True)
    os.makedirs(PATH_STAT, exist_ok=True)
    os.makedirs(PATH_PLOT, exist_ok=True)

    file_list = glob.glob(os.path.join(PATH_IN, '*.xdf'))
    if not file_list:
        print(f"Keine .xdf-Dateien in {PATH_IN} gefunden.")

    for file_path in file_list:
        try:
            preprocess_eeg_file(file_path, PATH_OUT, PATH_STAT, PATH_PLOT)
        except Exception as e:
            print(f"!!!!!! Fehler bei {os.path.basename(file_path)}: {e} !!!!!!")
